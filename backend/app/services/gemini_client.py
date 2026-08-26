"""Minimal REST client for the Gemini Interactions API.

Docs: https://ai.google.dev/gemini-api/docs/transcribe
Endpoint: POST https://generativelanguage.googleapis.com/v1beta/interactions
Auth header: x-goog-api-key: $GEMINI_API_KEY
"""

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

API_BASE_URL = os.getenv("GEMINI_API_BASE_URL", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class GeminiAPIError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = status_code in RETRYABLE_STATUS_CODES


def load_local_env() -> None:
    """Populate os.environ from local .env files (root and backend) without
    overriding values that are already set."""
    service_dir = Path(__file__).resolve()
    candidates = [
        Path.cwd() / ".env",
        Path.cwd() / "backend" / ".env",
        service_dir.parents[2] / ".env",  # backend/.env
        service_dir.parents[3] / ".env",  # repo root .env
    ]
    seen = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        with open(resolved, "r", encoding="utf-8") as env_file:
            for line in env_file:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_local_env()


def extract_text(interaction: Dict[str, Any]) -> str:
    """Pull the model's text output out of an Interactions API response."""
    output_text = interaction.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    parts: List[str] = []
    for step in interaction.get("steps") or []:
        if step.get("type") != "model_output":
            continue
        for block in step.get("content") or []:
            if block.get("type") == "text" and block.get("text"):
                parts.append(block["text"])
    return "\n".join(parts).strip()


class GeminiClient:
    """Thin async-friendly (blocking calls are cheap enough to wrap in
    asyncio.to_thread) client for the Gemini Interactions API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout_seconds: int = 60,
        max_retries: int = 3,
    ):
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        if not self.api_key:
            raise GeminiAPIError(
                "GEMINI_API_KEY is not set. Add it to .env or backend/.env "
                "(get one at https://aistudio.google.com/apikey)."
            )
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(1, max_retries)

    def create_interaction(
        self,
        model: str,
        input_blocks: List[Dict[str, Any]],
        generation_config: Optional[Dict[str, Any]] = None,
        response_format: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": model, "input": input_blocks}
        if generation_config:
            payload["generation_config"] = generation_config
        if response_format:
            payload["response_format"] = response_format
        interaction = self._request_with_retries(payload)

        status = interaction.get("status")
        if status and status != "completed":
            raise GeminiAPIError(
                f"Gemini API interaction status is '{status}' "
                "(output may be truncated; consider raising GEMINI_MAX_OUTPUT_TOKENS)"
            )
        return interaction

    def generate_text(
        self,
        prompt: str,
        model: str,
        temperature: float = 0,
        max_output_tokens: int = 4096,
        thinking_level: str = "",
        response_format: Optional[Dict[str, Any]] = None,
    ) -> str:
        generation_config: Dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        if thinking_level.strip():
            generation_config["thinking_level"] = thinking_level.strip()
        interaction = self.create_interaction(
            model=model,
            input_blocks=[{"type": "text", "text": prompt}],
            generation_config=generation_config,
            response_format=response_format,
        )
        return extract_text(interaction)

    def transcribe_audio(
        self,
        audio_bytes: bytes,
        model: str,
        mime_type: str = "audio/wav",
        prompt: str = "",
        custom_vocabulary: str = "",
        language: str = "",
    ) -> str:
        input_blocks: List[Dict[str, Any]] = [
            {
                "type": "audio",
                "data": base64.b64encode(audio_bytes).decode("utf-8"),
                "mime_type": mime_type,
            }
        ]
        instruction = prompt.strip()
        if custom_vocabulary.strip():
            instruction = (
                f"{instruction}\nUse this custom vocabulary when transcribing: {custom_vocabulary.strip()}"
            ).strip()
        if language.strip():
            instruction = f"{instruction}\nTranscribe in {language.strip()} language.".strip()
        if instruction:
            input_blocks.append({"type": "text", "text": instruction})

        interaction = self.create_interaction(model=model, input_blocks=input_blocks)
        return extract_text(interaction)

    def _request_with_retries(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        last_error: Optional[GeminiAPIError] = None
        for attempt in range(self.max_retries):
            if attempt > 0:
                delay = min(2 ** (attempt - 1), 4)
                logger.info("Retrying Gemini API in %ds (attempt %d/%d)", delay, attempt + 1, self.max_retries)
                time.sleep(delay)
            try:
                return self._post(body)
            except GeminiAPIError as exc:
                if not exc.retryable or attempt == self.max_retries - 1:
                    raise
                last_error = exc
                logger.warning("Gemini API attempt %d/%d failed: %s", attempt + 1, self.max_retries, exc)
        raise last_error or GeminiAPIError("Gemini API request failed")

    def _post(self, body: bytes) -> Dict[str, Any]:
        request = urllib.request.Request(
            f"{API_BASE_URL}/interactions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise GeminiAPIError(f"Gemini API HTTP {exc.code}: {detail[:500]}", status_code=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GeminiAPIError(f"Gemini API request failed: {exc}") from exc
