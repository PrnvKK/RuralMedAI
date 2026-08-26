"""Live consultation service backed by the Gemini API.

Replaces the previous local Gemma 4 / llama.cpp pipeline. Each VAD-detected
speech window is processed in two stages:

1. ``gemini-3.5-transcribe`` (Interactions API) converts the raw audio into a
   polished transcript — smart transcription removes filler words, handles
   medical jargon and 85+ languages.
2. ``gemini-3.5-flash`` extracts structured clinical field updates from the
   transcript using a JSON response schema.

Announcement: https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-5-transcribe/
Docs: https://ai.google.dev/gemini-api/docs/transcribe
"""

import array
import asyncio
import base64
import json
import logging
import math
import os
import tempfile
import wave
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.services.gemini_client import GeminiAPIError, GeminiClient

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
SAMPLE_WIDTH_BYTES = 2
CHANNELS = 1

LIST_FIELDS = {"symptoms", "medications", "allergies", "medical_history", "family_history", "procedures"}
SUPPORTED_FIELDS = {
    "name",
    "age",
    "gender",
    "chief_complaint",
    "symptoms",
    "medical_history",
    "family_history",
    "allergies",
    "medications",
    "procedures",
    "ration_card_type",
    "income",
    "occupation",
    "caste_category",
    "housing_type",
    "location",
    "tentative_doctor_diagnosis",
    "initial_llm_diagnosis",
    "transcript_summary",
    "vitals.temperature",
    "vitals.blood_pressure",
    "vitals.pulse",
    "vitals.spo2",
}

EXTRACTION_RESPONSE_FORMAT = {
    "type": "object",
    "properties": {
        "updates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string", "enum": sorted(SUPPORTED_FIELDS)},
                    "value": {},
                },
                "required": ["field", "value"],
            },
        }
    },
    "required": ["updates"],
}


@dataclass
class GeminiTranscribeConfig:
    transcribe_model: str = os.getenv("GEMINI_TRANSCRIBE_MODEL", "gemini-3.5-transcribe")
    extract_model: str = os.getenv("GEMINI_EXTRACT_MODEL", "gemini-3.5-flash")
    api_key: str = os.getenv("GEMINI_API_KEY", "")
    timeout_seconds: int = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "60"))
    max_output_tokens: int = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "4096"))
    thinking_level: str = os.getenv("GEMINI_THINKING_LEVEL", "low")
    transcribe_prompt: str = os.getenv("GEMINI_TRANSCRIBE_PROMPT", "Transcribe this audio.")
    custom_vocabulary: str = os.getenv("GEMINI_CUSTOM_VOCABULARY", "")
    language: str = os.getenv("GEMINI_LANGUAGE", "")
    min_rms: float = float(os.getenv("PARCHEE_MIN_RMS", "180"))
    vad_frame_ms: int = int(os.getenv("PARCHEE_VAD_FRAME_MS", "250"))
    vad_start_ms: int = int(os.getenv("PARCHEE_VAD_START_MS", "300"))
    vad_end_silence_ms: int = int(os.getenv("PARCHEE_VAD_END_SILENCE_MS", "900"))
    max_speech_seconds: int = int(os.getenv("PARCHEE_MAX_SPEECH_SECONDS", "5"))
    min_speech_ms: int = int(os.getenv("PARCHEE_MIN_SPEECH_MS", "700"))


class GeminiTranscribeService:
    def __init__(self, config: Optional[GeminiTranscribeConfig] = None, client: Optional[GeminiClient] = None):
        self.config = config or GeminiTranscribeConfig()
        self.client = client
        self.buffer = bytearray()
        self.vad_active = False
        self.speech_buffer = bytearray()
        self.pre_speech_buffer = bytearray()
        self.pending_speech_ms = 0
        self.trailing_silence_ms = 0
        self.patient_state: Dict[str, Any] = {}
        self.chunk_index = 0
        self.processing_queue: Optional[asyncio.Queue] = None
        self.worker_task: Optional[asyncio.Task] = None
        self.frame_bytes = max(
            SAMPLE_WIDTH_BYTES,
            int(SAMPLE_RATE * SAMPLE_WIDTH_BYTES * self.config.vad_frame_ms / 1000),
        )
        self.pre_speech_bytes = int(SAMPLE_RATE * SAMPLE_WIDTH_BYTES * 0.4)
        self.max_speech_bytes = self.config.max_speech_seconds * SAMPLE_RATE * SAMPLE_WIDTH_BYTES
        self.min_speech_bytes = int(SAMPLE_RATE * SAMPLE_WIDTH_BYTES * self.config.min_speech_ms / 1000)

    def _get_client(self) -> GeminiClient:
        if self.client is None:
            self.client = GeminiClient(
                api_key=self.config.api_key,
                timeout_seconds=self.config.timeout_seconds,
            )
        return self.client

    async def _safe_send_json(self, websocket: Any, data: dict) -> bool:
        try:
            await websocket.send_json(data)
            return True
        except Exception as exc:
            logger.debug("WS send failed (%s): %s", data.get("type", "?"), exc)
            return False

    async def handle_session(self, websocket: Any):
        try:
            from fastapi import WebSocketDisconnect
        except ImportError:
            WebSocketDisconnect = ConnectionError

        try:
            self._get_client()
        except GeminiAPIError as exc:
            await self._safe_send_json(websocket, {"type": "content", "text": f"{exc}\n"})
            return

        await self._safe_send_json(
            websocket,
            {
                "type": "content",
                "text": (
                    f"Parchee Edge connected to Gemini ({self.config.transcribe_model} + "
                    f"{self.config.extract_model}).\n"
                ),
            },
        )

        self.processing_queue = asyncio.Queue()
        self.worker_task = asyncio.create_task(self._processing_worker(websocket))

        try:
            while True:
                message = await websocket.receive_text()
                data = json.loads(message)

                if data.get("type") == "end_session":
                    await self._flush(websocket, final=True, force=True)
                    await self._enqueue_sentinel()
                    await self.worker_task
                    await asyncio.sleep(0.2)
                    await self._safe_send_json(websocket, {"type": "session_complete"})
                    return

                if "realtimeInput" not in data:
                    continue

                for media_chunk in data["realtimeInput"].get("mediaChunks", []):
                    self.buffer.extend(_decode_media_chunk(media_chunk.get("data", "")))

                await self._drain_vad_frames(websocket)
        except WebSocketDisconnect:
            logger.info("Client disconnected during session")
        finally:
            if self.worker_task and not self.worker_task.done():
                self.worker_task.cancel()
                try:
                    await self.worker_task
                except asyncio.CancelledError:
                    pass

    async def _processing_worker(self, websocket: Any):
        """Process speech chunks off the receive loop so slow Gemini calls
        never stall WebSocket keepalive pings."""
        assert self.processing_queue is not None
        while True:
            item = await self.processing_queue.get()
            try:
                if item is None:
                    return
                pcm_bytes, final = item
                await self._process_chunk(websocket, pcm_bytes, final=final)
            except Exception:
                logger.exception("Chunk worker error")
            finally:
                self.processing_queue.task_done()

    async def _enqueue_sentinel(self):
        if self.processing_queue is not None:
            await self.processing_queue.put(None)

    async def _flush(self, websocket: Any, final: bool, force: bool = False):
        if self.buffer:
            await self._drain_vad_frames(websocket, force_all=True)

        if not self.speech_buffer:
            self._reset_vad()
            return

        chunk = bytes(self.speech_buffer)
        self.buffer.clear()
        self._reset_vad()
        if force or len(chunk) >= self.min_speech_bytes:
            if self.processing_queue is not None:
                await self.processing_queue.put((chunk, final))
            else:
                await self._process_chunk(websocket, chunk, final=final)

    async def _drain_vad_frames(self, websocket: Any, force_all: bool = False):
        while len(self.buffer) >= self.frame_bytes or (force_all and self.buffer):
            frame = bytes(self.buffer[: self.frame_bytes])
            del self.buffer[: self.frame_bytes]
            await self._handle_vad_frame(websocket, frame)

    async def _handle_vad_frame(self, websocket: Any, frame: bytes):
        frame_ms = max(1, int(len(frame) / (SAMPLE_RATE * SAMPLE_WIDTH_BYTES) * 1000))
        voiced = not is_probably_silent(frame, self.config.min_rms, min_duration_ms=0)

        if not self.vad_active:
            if voiced:
                self.pending_speech_ms += frame_ms
                if self.pending_speech_ms >= self.config.vad_start_ms:
                    self.vad_active = True
                    self.speech_buffer.extend(self.pre_speech_buffer)
                    self.speech_buffer.extend(frame)
                    self.trailing_silence_ms = 0
                else:
                    self._remember_pre_speech(frame)
            else:
                self.pending_speech_ms = 0
                self._remember_pre_speech(frame)
            return

        self.speech_buffer.extend(frame)
        if voiced:
            self.trailing_silence_ms = 0
        else:
            self.trailing_silence_ms += frame_ms

        if len(self.speech_buffer) >= self.max_speech_bytes:
            await self._flush(websocket, final=False, force=True)
            return

        if self.trailing_silence_ms >= self.config.vad_end_silence_ms:
            trailing_bytes = int(SAMPLE_RATE * SAMPLE_WIDTH_BYTES * self.config.vad_end_silence_ms / 1000)
            chunk = bytes(self.speech_buffer[:-trailing_bytes] or self.speech_buffer)
            self._reset_vad()
            if len(chunk) >= self.min_speech_bytes:
                await self._process_chunk(websocket, chunk, final=False)

    def _remember_pre_speech(self, frame: bytes):
        self.pre_speech_buffer.extend(frame)
        if len(self.pre_speech_buffer) > self.pre_speech_bytes:
            del self.pre_speech_buffer[: len(self.pre_speech_buffer) - self.pre_speech_bytes]

    def _reset_vad(self):
        self.vad_active = False
        self.speech_buffer.clear()
        self.pre_speech_buffer.clear()
        self.pending_speech_ms = 0
        self.trailing_silence_ms = 0

    async def _process_chunk(self, websocket: Any, pcm_bytes: bytes, final: bool):
        self.chunk_index += 1
        if is_probably_silent(pcm_bytes, self.config.min_rms):
            await self._safe_send_json(
                websocket,
                {"type": "content", "text": f"Skipped silent audio window {self.chunk_index}.\n"},
            )
            return

        await self._safe_send_json(
            websocket,
            {
                "type": "content",
                "text": f"Processing audio chunk {self.chunk_index}{' (final)' if final else ''}...\n",
            },
        )

        # Keepalive: ping every 5s so the client/browser doesn't think the WebSocket died
        keepalive_running = True

        async def _ping_loop():
            while keepalive_running:
                await asyncio.sleep(5)
                if keepalive_running:
                    await self._safe_send_json(websocket, {"type": "heartbeat"})

        ping_task = asyncio.create_task(_ping_loop())

        try:
            transcript, updates = await asyncio.to_thread(self._run_pipeline, pcm_bytes)
        except Exception as exc:
            logger.exception("Gemini chunk processing failed")
            await self._safe_send_json(
                websocket,
                {"type": "content", "text": f"Gemini processing failed for chunk {self.chunk_index}: {exc}\n"},
            )
            return
        finally:
            keepalive_running = False
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass

        if transcript:
            await self._safe_send_json(
                websocket,
                {"type": "content", "text": f"Transcript {self.chunk_index}: {transcript}\n"},
            )

        for update in validate_updates(updates):
            merged_value = self._merge_update(update["field"], update["value"])
            await self._safe_send_json(websocket, {"type": "update", "field": update["field"], "value": merged_value})

    def _run_pipeline(self, pcm_bytes: bytes) -> Tuple[str, List[Dict[str, Any]]]:
        client = self._get_client()
        wav_bytes = _pcm_to_wav_bytes(pcm_bytes)

        transcript = client.transcribe_audio(
            wav_bytes,
            model=self.config.transcribe_model,
            mime_type="audio/wav",
            prompt=self.config.transcribe_prompt,
            custom_vocabulary=self.config.custom_vocabulary,
            language=self.config.language,
        ).strip()

        logger.info(
            "Gemini transcript: model=%s, length=%d, text=%r",
            self.config.transcribe_model,
            len(transcript),
            transcript[:300],
        )

        if not transcript:
            return "", []

        extraction = client.generate_text(
            prompt=build_extraction_prompt(self.patient_state, transcript),
            model=self.config.extract_model,
            temperature=0,
            max_output_tokens=self.config.max_output_tokens,
            thinking_level=self.config.thinking_level,
            response_format=EXTRACTION_RESPONSE_FORMAT,
        )
        parsed = parse_extraction_response(extraction)
        return transcript, parsed.get("updates", [])

    def _merge_update(self, field: str, value: Any) -> Any:
        if field in LIST_FIELDS:
            incoming = value if isinstance(value, list) else [value]
            existing = self.patient_state.get(field, [])
            if not isinstance(existing, list):
                existing = [existing]

            merged: List[str] = []
            seen = set()
            for item in [*existing, *incoming]:
                if item is None:
                    continue
                normalized = str(item).strip()
                if not normalized:
                    continue
                key = normalized.lower()
                if key not in seen:
                    seen.add(key)
                    merged.append(normalized)

            self.patient_state[field] = merged
            return merged

        self.patient_state[field] = value
        return value


def _decode_media_chunk(data: str) -> bytes:
    return base64.b64decode(data)


def build_extraction_prompt(patient_state: Dict[str, Any], transcript: str) -> str:
    compact_state = {key: value for key, value in patient_state.items() if value not in (None, "", []) and value != {}}
    return f"""You are a clinical documentation assistant for a rural clinic consultation.
Extract structured clinical field updates from the transcript below.

Supported fields: {", ".join(sorted(SUPPORTED_FIELDS))}

Already known patient state (do not repeat unchanged values): {json.dumps(compact_state, ensure_ascii=False)}

Rules:
- Only include fields explicitly stated or clearly implied in the transcript.
- Use list values for: {", ".join(sorted(LIST_FIELDS))}.
- Use plain strings for everything else. Vitals use dotted fields (e.g. vitals.temperature).
- Return ONLY valid JSON, no markdown, no explanation.

Transcript:
{transcript}

Format: {{"updates": [{{"field": "...", "value": "..."}}]}}"""


def parse_extraction_response(text: str) -> Dict[str, Any]:
    stripped = strip_thinking(text).strip()
    if not stripped:
        return {"transcript": "", "updates": []}

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            logger.warning("Model response did not contain JSON; treating as no extraction: %r", stripped[:500])
            return {"transcript": "", "updates": []}
        try:
            return json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            logger.warning("Model response contained invalid JSON; treating as no extraction: %r", stripped[:500])
            return {"transcript": "", "updates": []}


def strip_thinking(text: str) -> str:
    cleaned = text
    while True:
        start = cleaned.find("<think>")
        end = cleaned.find("</think>")
        if start == -1 or end == -1 or end < start:
            break
        cleaned = cleaned[:start] + cleaned[end + len("</think>") :]
    return cleaned


def validate_updates(raw_updates: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_updates, list):
        return []

    updates: List[Dict[str, Any]] = []
    for item in raw_updates:
        if not isinstance(item, dict):
            continue

        field = item.get("field")
        value = item.get("value")
        if field not in SUPPORTED_FIELDS or value in (None, ""):
            continue

        if field in LIST_FIELDS and isinstance(value, str):
            value = [part.strip() for part in value.split(",") if part.strip()]

        updates.append({"field": field, "value": value})

    return updates


def _pcm_to_wav_bytes(pcm_bytes: bytes) -> bytes:
    with tempfile.SpooledTemporaryFile() as wav_file:
        with wave.open(wav_file, "wb") as writer:
            writer.setnchannels(CHANNELS)
            writer.setsampwidth(SAMPLE_WIDTH_BYTES)
            writer.setframerate(SAMPLE_RATE)
            writer.writeframes(pcm_bytes)

        wav_file.seek(0)
        return wav_file.read()


def is_probably_silent(pcm_bytes: bytes, min_rms: float, min_duration_ms: int = 500) -> bool:
    min_bytes = int(SAMPLE_RATE * SAMPLE_WIDTH_BYTES * min_duration_ms / 1000)
    if min_duration_ms and len(pcm_bytes) < min_bytes:
        return True

    samples = array.array("h")
    samples.frombytes(pcm_bytes[: len(pcm_bytes) - (len(pcm_bytes) % SAMPLE_WIDTH_BYTES)])
    if not samples:
        return True

    step = max(1, len(samples) // 12000)
    sampled = samples[::step]
    square_sum = sum(sample * sample for sample in sampled)
    rms = math.sqrt(square_sum / len(sampled))
    return rms < min_rms
