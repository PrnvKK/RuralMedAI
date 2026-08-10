import asyncio
import array
import base64
import json
import logging
import math
import os
import shlex
import subprocess
import tempfile
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

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


def _load_local_env():
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.getcwd(), "backend", ".env"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as env_file:
            for line in env_file:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_local_env()


@dataclass
class LlamaCppConfig:
    mode: str = os.getenv("LLAMA_CPP_MODE", "auto").lower()
    base_url: str = os.getenv("LLAMA_CPP_BASE_URL", "http://localhost:8085").rstrip("/")
    model_name: str = os.getenv("LLAMA_CPP_MODEL_NAME", "gemma-4")
    cli_path: Optional[str] = os.getenv("LLAMA_CPP_CLI_PATH")
    cli_args: str = os.getenv("LLAMA_CPP_CLI_ARGS", "")
    chunk_seconds: int = int(os.getenv("PARCHEE_CHUNK_SECONDS", "10"))
    timeout_seconds: int = int(os.getenv("LLAMA_CPP_TIMEOUT_SECONDS", "180"))
    max_tokens: int = int(os.getenv("LLAMA_CPP_MAX_TOKENS", "512"))
    min_rms: float = float(os.getenv("PARCHEE_MIN_RMS", "180"))
    vad_frame_ms: int = int(os.getenv("PARCHEE_VAD_FRAME_MS", "250"))
    vad_start_ms: int = int(os.getenv("PARCHEE_VAD_START_MS", "300"))
    vad_end_silence_ms: int = int(os.getenv("PARCHEE_VAD_END_SILENCE_MS", "900"))
    max_speech_seconds: int = int(os.getenv("PARCHEE_MAX_SPEECH_SECONDS", "12"))
    min_speech_ms: int = int(os.getenv("PARCHEE_MIN_SPEECH_MS", "700"))
    cli_threads: Optional[int] = (
        int(os.getenv("LLAMA_CPP_THREADS")) if os.getenv("LLAMA_CPP_THREADS") else None
    )


class LlamaCppGemmaService:
    def __init__(self, config: Optional[LlamaCppConfig] = None):
        self.config = config or LlamaCppConfig()
        self.buffer = bytearray()
        self.vad_active = False
        self.speech_buffer = bytearray()
        self.pre_speech_buffer = bytearray()
        self.pending_speech_ms = 0
        self.trailing_silence_ms = 0
        self.patient_state: Dict[str, Any] = {}
        self.active_mode: Optional[str] = None
        self.chunk_index = 0
        self.frame_bytes = max(
            SAMPLE_WIDTH_BYTES,
            int(SAMPLE_RATE * SAMPLE_WIDTH_BYTES * self.config.vad_frame_ms / 1000),
        )
        self.pre_speech_bytes = int(SAMPLE_RATE * SAMPLE_WIDTH_BYTES * 0.4)
        self.max_speech_bytes = (
            self.config.max_speech_seconds * SAMPLE_RATE * SAMPLE_WIDTH_BYTES
        )
        self.min_speech_bytes = int(
            SAMPLE_RATE * SAMPLE_WIDTH_BYTES * self.config.min_speech_ms / 1000
        )

    async def _safe_send_json(self, websocket: Any, data: dict) -> bool:
        try:
            await websocket.send_json(data)
            return True
        except Exception:
            return False

    async def handle_session(self, websocket: Any):
        self.active_mode = await self._resolve_mode()
        await self._safe_send_json(
            websocket,
            {
                "type": "content",
                "text": f"Parchee Edge connected to local Gemma via llama.cpp ({self.active_mode} mode).\n",
            }
        )

        while True:
            message = await websocket.receive_text()
            data = json.loads(message)

            if data.get("type") == "end_session":
                await self._flush(websocket, final=True, force=True)
                await self._safe_send_json(websocket, {"type": "session_complete"})
                return

            if "realtimeInput" not in data:
                continue

            for media_chunk in data["realtimeInput"].get("mediaChunks", []):
                self.buffer.extend(base64.b64decode(media_chunk.get("data", "")))

            await self._drain_vad_frames(websocket)

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
            trailing_bytes = int(
                SAMPLE_RATE
                * SAMPLE_WIDTH_BYTES
                * self.config.vad_end_silence_ms
                / 1000
            )
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

    async def _resolve_mode(self) -> str:
        if self.config.mode == "server":
            return "server"
        if self.config.mode == "cli":
            return "cli"

        if await asyncio.to_thread(self._server_supports_audio):
            return "server"

        logger.warning(
            "llama-server audio probe failed. Falling back to llama-mtmd-cli; latency may be higher."
        )
        return "cli"

    def _server_supports_audio(self) -> bool:
        silence = b"\x00" * SAMPLE_RATE * SAMPLE_WIDTH_BYTES
        wav_b64 = base64.b64encode(_pcm_to_wav_bytes(silence)).decode("utf-8")
        payload = {
            "model": self.config.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Return only the word ok."},
                        {
                            "type": "input_audio",
                            "input_audio": {"data": wav_b64, "format": "wav"},
                        },
                    ],
                }
            ],
            "max_tokens": 4,
            "temperature": 0,
        }

        try:
            self._post_chat_completions(payload, timeout=20)
            return True
        except Exception as exc:
            logger.info("llama-server audio probe did not pass: %s", exc)
            return False

    async def _process_chunk(self, websocket: Any, pcm_bytes: bytes, final: bool):
        self.chunk_index += 1
        if is_probably_silent(pcm_bytes, self.config.min_rms):
            await self._safe_send_json(
                websocket,
                {
                    "type": "content",
                    "text": f"Skipped silent audio window {self.chunk_index}.\n",
                }
            )
            return

        await self._safe_send_json(
            websocket,
            {
                "type": "content",
                "text": f"Processing audio chunk {self.chunk_index}{' (final)' if final else ''}...\n",
            }
        )

        try:
            result_text = await asyncio.to_thread(self._run_inference, pcm_bytes)
            extraction = parse_extraction_response(result_text)
        except Exception as exc:
            logger.exception("Gemma chunk processing failed")
            await self._safe_send_json(
                websocket,
                {
                    "type": "content",
                    "text": f"Local Gemma processing failed for chunk {self.chunk_index}: {exc}\n",
                }
            )
            return

        transcript = extraction.get("transcript")
        if transcript:
            await self._safe_send_json(
                websocket,
                {
                    "type": "content",
                    "text": f"Transcript {self.chunk_index}: {transcript}\n",
                }
            )

        for update in validate_updates(extraction.get("updates", [])):
            merged_value = self._merge_update(update["field"], update["value"])
            await self._safe_send_json(
                websocket,
                {"type": "update", "field": update["field"], "value": merged_value}
            )

    def _run_inference(self, pcm_bytes: bytes) -> str:
        wav_bytes = _pcm_to_wav_bytes(pcm_bytes)
        prompt = build_extraction_prompt(self.patient_state)

        if self.active_mode == "server":
            audio_b64 = base64.b64encode(wav_bytes).decode("utf-8")
            payload = {
                "model": self.config.model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "input_audio",
                                "input_audio": {"data": audio_b64, "format": "wav"},
                            },
                        ],
                    }
                ],
                "temperature": 0,
                "max_tokens": self.config.max_tokens,
                "reasoning_format": "none",
                "chat_template_kwargs": {"enable_thinking": False},
            }
            response = self._post_chat_completions(
                payload, timeout=self.config.timeout_seconds
            )
            content = response["choices"][0]["message"]["content"]
            finish_reason = response["choices"][0].get("finish_reason", "unknown")
            logger.info("Gemma response: finish_reason=%s, length=%d, raw=%r",
                        finish_reason, len(content), content[:500])
            return content

        if not self.config.cli_path:
            raise RuntimeError("LLAMA_CPP_CLI_PATH is required for cli fallback mode")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as audio_file:
            audio_file.write(wav_bytes)
            audio_path = audio_file.name

        try:
            command = [
                self.config.cli_path,
                *shlex.split(self.config.cli_args),
                "--audio",
                audio_path,
                "-p",
                prompt,
                "-n",
                str(self.config.max_tokens),
                "--jinja",
                "--no-warmup",
            ]
            if self.config.cli_threads:
                command.extend(["-t", str(self.config.cli_threads)])

            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
                check=True,
            )
            return completed.stdout.strip()
        finally:
            try:
                os.unlink(audio_path)
            except OSError:
                pass

    def _post_chat_completions(self, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.base_url}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"llama-server HTTP {exc.code}: {detail}") from exc

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


def build_extraction_prompt(patient_state: Dict[str, Any]) -> str:
    compact_state = {
        key: value
        for key, value in patient_state.items()
        if value not in (None, "", []) and value != {}
    }
    return f"""
You are Parchee Edge, an offline clinical documentation assistant.
Listen to this 16 kHz mono consultation audio chunk and return ONLY valid JSON.
Do not think step by step. Do not output hidden reasoning, chain-of-thought, markdown,
XML tags, or <think> blocks. Your entire response must be a single JSON object.

Goals:
- Transcribe the segment in its original language/code-switching.
- Extract only fields supported by the schema below.
- Capture welfare/claim eligibility facts such as ration card, income, occupation, caste category, housing, and location when spoken.
- Normalize colloquial clinical phrases when useful.
- Label diagnosis as documentation support. Do not present autonomous medical advice.
- If unsure, omit the field.

Supported fields:
name, age, gender, chief_complaint, symptoms, medical_history, family_history,
allergies, medications, procedures, ration_card_type, income, occupation,
caste_category, housing_type, location, tentative_doctor_diagnosis,
initial_llm_diagnosis, transcript_summary, vitals.temperature,
vitals.blood_pressure, vitals.pulse, vitals.spo2

Current accumulated patient state:
{json.dumps(compact_state, ensure_ascii=False)}

Return shape:
{{
  "transcript": "single line transcript",
  "updates": [
    {{"field": "chief_complaint", "value": "fever for 3 days"}},
    {{"field": "symptoms", "value": ["fever", "cough"]}}
  ]
}}
""".strip()


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
        return json.loads(stripped[start : end + 1])


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


def is_probably_silent(
    pcm_bytes: bytes, min_rms: float, min_duration_ms: int = 500
) -> bool:
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
