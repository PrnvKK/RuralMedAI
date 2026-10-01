"""Low-latency consultation pipeline: PCM -> Whisper -> Gemma -> live form."""

import array
import asyncio
import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)
# Browser PCM arrives at 16 kHz mono 16-bit; both VAD windows and Whisper
# expect this exact framing, so every buffer is measured in these units.
SAMPLE_RATE, SAMPLE_WIDTH_BYTES, CHANNELS = 16000, 2, 1
LIST_FIELDS = {"symptoms"}
SUPPORTED_FIELDS = {
    "name",
    "age",
    "gender",
    "chief_complaint",
    "symptoms",
    "ration_card_type",
    "income",
    "occupation",
    "caste_category",
    "housing_type",
    "location",
    "tentative_doctor_diagnosis",
    "vitals.temperature",
    "vitals.blood_pressure",
    "vitals.pulse",
    "vitals.spo2",
}
FLAT_FIELD_MAP = {
    "blood_pressure": "vitals.blood_pressure",
    "pulse": "vitals.pulse",
    "temperature": "vitals.temperature",
    "spo2": "vitals.spo2",
    "oxygen_saturation": "vitals.spo2",
    "saturation": "vitals.spo2",
    "tentative_diagnosis": "tentative_doctor_diagnosis",
}
FIELD_RE = re.compile(r'"([a-z0-9_]+)"\s*:\s*("(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?|\[[^\]]*\])\s*[,}]')
EXTRACTION_GRAMMAR = r"""root ::= "{" ws (pair (ws "," ws pair)*)? ws "}"
pair ::= key ws ":" ws value
key ::= "\"" ("name" | "age" | "gender" | "chief_complaint" | "symptoms" |
              "blood_pressure" | "pulse" | "temperature" | "spo2" |
              "oxygen_saturation" | "saturation" | "ration_card_type" |
              "income" | "occupation" | "caste_category" | "housing_type" |
              "location" | "tentative_diagnosis") "\""
value ::= string | number | symptomslist
string ::= "\"" [^"]* "\""
number ::= "-"? [0-9]+ ("." [0-9]+)?
symptomslist ::= "[" ws (string (ws "," ws string)*)? ws "]"
ws ::= [ \t\n]*"""


@dataclass
class LlamaCppConfig:
    base_url: str = os.getenv("LLAMA_CPP_BASE_URL", "http://127.0.0.1:8085").rstrip("/")
    model_name: str = os.getenv("LLAMA_CPP_MODEL_NAME", "gemma-3-4b-it")
    # Optional bearer token for hosted OpenAI-compatible endpoints. Local
    # llama.cpp ignores it, so one config works for both deployment modes.
    api_key: Optional[str] = os.getenv("LLAMA_CPP_API_KEY") or None
    timeout_seconds: int = int(os.getenv("LLAMA_CPP_TIMEOUT_SECONDS", "90"))
    max_tokens: int = int(os.getenv("LLAMA_CPP_MAX_TOKENS", "512"))
    whisper_binary: str = os.getenv("WHISPER_CPP_BINARY", "whisper_cpp/whisper-cli.exe")
    whisper_model: str = os.getenv("WHISPER_CPP_MODEL", "whisper_cpp/models/ggml-base.en.bin")
    whisper_threads: int = int(os.getenv("WHISPER_CPP_THREADS", "4"))
    min_rms: float = float(os.getenv("PARCHEE_MIN_RMS", "180"))
    vad_frame_ms: int = int(os.getenv("PARCHEE_VAD_FRAME_MS", "250"))
    vad_start_ms: int = int(os.getenv("PARCHEE_VAD_START_MS", "300"))
    vad_end_silence_ms: int = int(os.getenv("PARCHEE_VAD_END_SILENCE_MS", "600"))
    asr_slice_seconds: float = float(os.getenv("PARCHEE_ASR_SLICE_SECONDS", "2"))
    max_speech_seconds: int = int(os.getenv("PARCHEE_MAX_SPEECH_SECONDS", "8"))
    overlap_ms: int = int(os.getenv("PARCHEE_AUDIO_OVERLAP_MS", "300"))
    min_speech_ms: int = int(os.getenv("PARCHEE_MIN_SPEECH_MS", "700"))


class LlamaCppGemmaService:
    def __init__(self, config: Optional[LlamaCppConfig] = None):
        self.config = config or LlamaCppConfig()
        # Three ring buffers: raw incoming PCM, the current active speech window,
        # and a short pre-roll so the VAD never clips the first words.
        self.buffer, self.speech_buffer, self.pre_speech_buffer = bytearray(), bytearray(), bytearray()
        self.vad_active = False
        self.pending_speech_ms = self.trailing_silence_ms = self.active_speech_ms = self.chunk_index = 0
        self.patient_state: Dict[str, Any] = {}
        self._last_transcript = ""
        self._pending_extraction_text: List[str] = []
        self._audio_queue: Optional[asyncio.Queue] = None
        self._extraction_queue: Optional[asyncio.Queue] = None
        self._workers: List[asyncio.Task] = []
        self._send_lock: Optional[asyncio.Lock] = None
        self.frame_bytes = max(
            SAMPLE_WIDTH_BYTES, int(SAMPLE_RATE * SAMPLE_WIDTH_BYTES * self.config.vad_frame_ms / 1000)
        )
        self.pre_speech_bytes = int(SAMPLE_RATE * SAMPLE_WIDTH_BYTES * 0.4)
        self.max_speech_bytes = self.config.max_speech_seconds * SAMPLE_RATE * SAMPLE_WIDTH_BYTES
        self.asr_slice_bytes = max(
            SAMPLE_WIDTH_BYTES, int(self.config.asr_slice_seconds * SAMPLE_RATE * SAMPLE_WIDTH_BYTES)
        )
        self.overlap_bytes = int(self.config.overlap_ms * SAMPLE_RATE * SAMPLE_WIDTH_BYTES / 1000)
        self.min_speech_bytes = int(SAMPLE_RATE * SAMPLE_WIDTH_BYTES * self.config.min_speech_ms / 1000)

    async def _safe_send_json(self, websocket: Any, data: dict) -> bool:
        try:
            if self._send_lock is None:
                await websocket.send_json(data)
            else:
                async with self._send_lock:
                    await websocket.send_json(data)
            return True
        except Exception as exc:
            logger.debug("WebSocket send failed (%s): %s", data.get("type", "?"), exc)
            return False

    async def handle_session(self, websocket: Any):
        from fastapi import WebSocketDisconnect

        self._start_workers(websocket)
        await self._safe_send_json(
            websocket, {"type": "content", "text": "Parchee Edge ready: local Whisper ASR + Gemma extraction.\n"}
        )
        try:
            while True:
                data = json.loads(await websocket.receive_text())
                if data.get("type") == "end_session":
                    await self._flush(websocket, final=True, force=True)
                    await self._drain_workers()
                    await self._safe_send_json(websocket, {"type": "session_complete"})
                    return
                for media_chunk in data.get("realtimeInput", {}).get("mediaChunks", []):
                    self.buffer.extend(base64.b64decode(media_chunk.get("data", "")))
                await self._drain_vad_frames(websocket)
        except WebSocketDisconnect:
            logger.info("Client disconnected during live consultation")
        finally:
            await self._stop_workers(cancel=True)

    def _start_workers(self, websocket: Any) -> None:
        # Two independent pipelines: ASR transcribes audio while extraction asks
        # Gemma for fields. Decoupling them means a slow model pass never stalls
        # the next transcription, keeping the live form responsive.
        self._audio_queue = asyncio.Queue()
        self._extraction_queue = asyncio.Queue()
        self._send_lock = asyncio.Lock()
        self._workers = [
            asyncio.create_task(self._asr_worker(websocket), name="parchee-asr"),
            asyncio.create_task(self._extraction_worker(websocket), name="parchee-extraction"),
        ]

    async def _stop_workers(self, cancel: bool = False) -> None:
        if not self._workers:
            return
        if cancel:
            for worker in self._workers:
                worker.cancel()
        else:
            assert self._audio_queue is not None and self._extraction_queue is not None
            await self._audio_queue.put(None)
            await self._extraction_queue.put(None)
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    async def _drain_workers(self) -> None:
        assert self._audio_queue is not None and self._extraction_queue is not None
        await self._audio_queue.join()
        await self._extraction_queue.join()
        await self._stop_workers()

    async def _flush(self, websocket: Any, final: bool, force: bool = False):
        if self.buffer:
            await self._drain_vad_frames(websocket, force_all=True)
        chunk = bytes(self.speech_buffer)
        self.buffer.clear()
        self._reset_vad()
        if chunk and (force or len(chunk) >= self.min_speech_bytes):
            await self._enqueue_audio(chunk, "final" if final else "flush", final=final)

    async def _drain_vad_frames(self, websocket: Any, force_all: bool = False):
        while len(self.buffer) >= self.frame_bytes or (force_all and self.buffer):
            frame = bytes(self.buffer[: self.frame_bytes])
            del self.buffer[: self.frame_bytes]
            await self._handle_vad_frame(websocket, frame)

    async def _handle_vad_frame(self, websocket: Any, frame: bytes):
        frame_ms = max(1, int(len(frame) / (SAMPLE_RATE * SAMPLE_WIDTH_BYTES) * 1000))
        voiced = not is_probably_silent(frame, self.config.min_rms, min_duration_ms=0)
        # Waiting state: accumulate energy until speech starts, keep the pre-roll.
        if not self.vad_active:
            if voiced:
                self.pending_speech_ms += frame_ms
                if self.pending_speech_ms >= self.config.vad_start_ms:
                    self.vad_active = True
                    self.active_speech_ms = frame_ms
                    self.speech_buffer.extend(self.pre_speech_buffer)
                    self.speech_buffer.extend(frame)
                else:
                    self._remember_pre_speech(frame)
            else:
                self.pending_speech_ms = 0
                self._remember_pre_speech(frame)
            return
        # Active state: emit on forced max duration, end-of-speech silence, or a
        # timed ASR slice (streaming) — whichever fires first.
        self.speech_buffer.extend(frame)
        self.active_speech_ms += frame_ms
        self.trailing_silence_ms = 0 if voiced else self.trailing_silence_ms + frame_ms
        if self.active_speech_ms >= self.config.max_speech_seconds * 1000:
            await self._emit_active_slice("forced")
            return
        if self.trailing_silence_ms >= self.config.vad_end_silence_ms:
            chunk = bytes(self.speech_buffer)
            self._reset_vad()
            if len(chunk) >= self.min_speech_bytes:
                await self._enqueue_audio(chunk, "silence")
            return
        if len(self.speech_buffer) >= self.asr_slice_bytes:
            await self._emit_active_slice("timed")

    def _remember_pre_speech(self, frame: bytes):
        self.pre_speech_buffer.extend(frame)
        if len(self.pre_speech_buffer) > self.pre_speech_bytes:
            del self.pre_speech_buffer[: -self.pre_speech_bytes]

    def _reset_vad(self):
        self.vad_active = False
        self.speech_buffer.clear()
        self.pre_speech_buffer.clear()
        self.pending_speech_ms = self.trailing_silence_ms = self.active_speech_ms = 0

    async def _emit_active_slice(self, reason: str) -> None:
        chunk = bytes(self.speech_buffer)
        if len(chunk) < self.min_speech_bytes:
            return
        await self._enqueue_audio(chunk, reason)
        # Keep an audio tail so Whisper does not lose words across a timed cut.
        tail = self.speech_buffer[-self.overlap_bytes :] if self.overlap_bytes else b""
        self.speech_buffer = bytearray(tail)
        self.trailing_silence_ms = 0
        if reason == "forced":
            self.active_speech_ms = self.config.overlap_ms

    async def _enqueue_audio(self, pcm_bytes: bytes, reason: str, final: bool = False) -> None:
        if is_probably_silent(pcm_bytes, self.config.min_rms):
            return
        self.chunk_index += 1
        assert self._audio_queue is not None
        await self._audio_queue.put((self.chunk_index, pcm_bytes, reason, final))

    async def _asr_worker(self, websocket: Any) -> None:
        assert self._audio_queue is not None
        while True:
            job = await self._audio_queue.get()
            try:
                if job is None:
                    return
                index, pcm_bytes, reason, final = job
                await self._safe_send_json(
                    websocket, {"type": "content", "text": f"Transcribing audio chunk {index}...\n"}
                )
                raw_transcript = await asyncio.to_thread(self._transcribe, pcm_bytes)
                transcript = deduplicate_overlap(self._last_transcript, raw_transcript)
                if not transcript:
                    continue
                self._last_transcript = raw_transcript.strip()
                await self._safe_send_json(
                    websocket, {"type": "content", "text": f"Transcript {index}: {transcript}\n"}
                )
                await self._commit_transcript(transcript, reason, final)
            except Exception as exc:
                logger.exception("Whisper transcription failed for audio chunk %s", job[0] if job else "?")
                await self._safe_send_json(
                    websocket, {"type": "content", "text": f"Local transcription failed: {exc}\n"}
                )
            finally:
                self._audio_queue.task_done()

    async def _commit_transcript(self, transcript: str, reason: str, final: bool) -> None:
        # Buffer short fragments until the sentence ends (punctuation or a VAD
        # boundary) so the extractor sees a meaningful chunk, not half a thought.
        self._pending_extraction_text.append(transcript)
        is_sentence_end = bool(re.search(r"[.!?…][\"')\]]*\s*$", transcript))
        if not (is_sentence_end or reason in {"silence", "forced", "final", "flush"} or final):
            return
        text = " ".join(self._pending_extraction_text).strip()
        self._pending_extraction_text.clear()
        if text:
            assert self._extraction_queue is not None
            await self._extraction_queue.put(text)

    async def _extraction_worker(self, websocket: Any) -> None:
        assert self._extraction_queue is not None
        while True:
            transcript = await self._extraction_queue.get()
            try:
                if transcript is None:
                    return
                await self._stream_and_apply(websocket, transcript)
            except Exception as exc:
                logger.exception("Gemma extraction failed for transcript batch")
                await self._safe_send_json(
                    websocket, {"type": "content", "text": f"Local form extraction failed: {exc}\n"}
                )
            finally:
                self._extraction_queue.task_done()

    def _transcribe(self, pcm_bytes: bytes) -> str:
        binary, model = Path(self.config.whisper_binary), Path(self.config.whisper_model)
        if not binary.exists() or not model.exists():
            raise FileNotFoundError(
                "Whisper runtime is missing. Run `python scripts/setup.py` or "
                "set WHISPER_CPP_BINARY and WHISPER_CPP_MODEL."
            )
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as audio_file:
            audio_file.write(_pcm_to_wav_bytes(pcm_bytes))
            audio_path = Path(audio_file.name)
        try:
            result = subprocess.run(
                [
                    str(binary),
                    "-m",
                    str(model),
                    "-f",
                    str(audio_path),
                    "--no-timestamps",
                    "-nt",
                    "-t",
                    str(self.config.whisper_threads),
                ],
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
                check=True,
            )
            return " ".join(
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip() and not line.lstrip().startswith("[")
            )
        finally:
            audio_path.unlink(missing_ok=True)

    async def _stream_and_apply(self, websocket: Any, transcript: str):
        loop, queue = asyncio.get_running_loop(), asyncio.Queue()
        task = asyncio.create_task(
            asyncio.to_thread(
                self._stream_extraction, transcript, lambda fields: loop.call_soon_threadsafe(queue.put_nowait, fields)
            )
        )
        while not task.done() or not queue.empty():
            try:
                fields = await asyncio.wait_for(queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            for field, value in fields.items():
                if field in SUPPORTED_FIELDS:
                    await self._safe_send_json(
                        websocket, {"type": "update", "field": field, "value": self._merge_update(field, value)}
                    )
        await task

    def _stream_extraction(self, transcript: str, emit: Callable[[Dict[str, Any]], None]):
        payload = {
            "model": self.config.model_name,
            "messages": [{"role": "user", "content": build_extraction_prompt(transcript)}],
            "temperature": 0.1,
            "max_tokens": self.config.max_tokens,
            "stream": True,
            "grammar": EXTRACTION_GRAMMAR,
        }
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        request = urllib.request.Request(
            f"{self.config.base_url}/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        buffer, emitted = "", set()
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    try:
                        delta = json.loads(line[6:])["choices"][0].get("delta", {}).get("content") or ""
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if not isinstance(delta, str):
                        continue
                    buffer += delta
                    fields = {key: value for key, value in parse_partial_fields(buffer).items() if key not in emitted}
                    if fields:
                        emitted.update(fields)
                        emit(fields)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"llama-server HTTP {exc.code}: {exc.read().decode(errors='replace')}") from exc
        logger.info("Gemma extraction completed: %d characters, %d fields", len(buffer), len(emitted))

    def _merge_update(self, field: str, value: Any) -> Any:
        if field in LIST_FIELDS:
            incoming, existing = (value if isinstance(value, list) else [value]), self.patient_state.get(field, [])
            seen, merged = set(), []
            for item in [*(existing if isinstance(existing, list) else [existing]), *incoming]:
                item = str(item).strip()
                if item and item.lower() not in seen:
                    seen.add(item.lower())
                    merged.append(item)
            self.patient_state[field] = merged
            return merged
        self.patient_state[field] = value
        return value


def build_extraction_prompt(transcript: str) -> str:
    return f"""Convert the doctor's dictation transcript into JSON. Output ONLY one single-line JSON object
containing ONLY fields explicitly present. Available keys: name, age, gender, chief_complaint, symptoms
(array of strings), blood_pressure, pulse, temperature, spo2, ration_card_type, income, occupation,
caste_category, housing_type, location, tentative_diagnosis. Omit absent fields; never output null or empty
values; normalize BP as "120/80" and temperature as a plain number.

Transcript: {json.dumps(transcript, ensure_ascii=False)}

JSON:"""


def parse_partial_fields(text: str) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    for key, raw_value in FIELD_RE.findall(text):
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            continue
        field = FLAT_FIELD_MAP.get(key, key)
        if field in SUPPORTED_FIELDS and value not in (None, "", []):
            fields[field] = value
    return fields


def parse_extraction_response(text: str) -> Dict[str, Any]:
    return {"transcript": "", "updates": [{"field": k, "value": v} for k, v in parse_partial_fields(text).items()]}


def deduplicate_overlap(previous: str, current: str) -> str:
    """Remove the repeated token prefix caused by overlapping audio slices."""
    current = current.strip()
    if not previous or not current:
        return current
    previous_words, current_words = previous.split(), current.split()
    normalized_previous = [re.sub(r"\W+", "", word).lower() for word in previous_words]
    normalized_current = [re.sub(r"\W+", "", word).lower() for word in current_words]
    max_overlap = min(len(normalized_previous), len(normalized_current))
    for size in range(max_overlap, 0, -1):
        if normalized_previous[-size:] == normalized_current[:size]:
            return " ".join(current_words[size:]).strip()
    return current


def validate_updates(raw_updates: Any) -> List[Dict[str, Any]]:
    updates = []
    for item in raw_updates:
        if not isinstance(item, dict) or item.get("field") not in SUPPORTED_FIELDS or item.get("value") in (None, ""):
            continue
        value = item["value"]
        if item["field"] in LIST_FIELDS and isinstance(value, str):
            value = [part.strip() for part in value.split(",") if part.strip()]
        updates.append({"field": item["field"], "value": value})
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
    if min_duration_ms and len(pcm_bytes) < SAMPLE_RATE * SAMPLE_WIDTH_BYTES * min_duration_ms / 1000:
        return True
    samples = array.array("h")
    samples.frombytes(pcm_bytes[: len(pcm_bytes) - len(pcm_bytes) % SAMPLE_WIDTH_BYTES])
    if not samples:
        return True
    return (sum(sample * sample for sample in samples) / len(samples)) ** 0.5 < min_rms
