"""Live validation for the Gemini 3.5 Transcribe integration.

Usage (from repository root, any Python >= 3.12, stdlib only):

    python scripts/validate_gemini.py path/to/audio.wav

Runs four checks against the real Gemini API using GEMINI_API_KEY:
  1. Text smoke test        (gemini-3.5-flash / GEMINI_EXTRACT_MODEL)
  2. Audio transcription    (gemini-3.5-transcribe / GEMINI_TRANSCRIBE_MODEL)
  3. Clinical extraction    (structured JSON updates from the transcript)
  4. Full service pipeline  (GeminiTranscribeService._process_chunk)
"""

import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.gemini_client import GeminiClient  # noqa: E402
from app.services.gemini_transcribe_service import (  # noqa: E402
    EXTRACTION_RESPONSE_FORMAT,
    GeminiTranscribeConfig,
    GeminiTranscribeService,
    build_extraction_prompt,
    parse_extraction_response,
    validate_updates,
)


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)
        return True


def main() -> int:
    wav_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    print("=" * 70)
    print("Parchee Edge - Gemini 3.5 Transcribe live validation")
    print("=" * 70)

    try:
        client = GeminiClient()
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"[ok] API key loaded ({client.api_key[:6]}...{client.api_key[-4:]})")

    print("\n--- Step 1: text smoke test ---")
    try:
        extract_model = GeminiTranscribeConfig().extract_model
        reply = client.generate_text(
            prompt="Reply with exactly: OK",
            model=extract_model,
            max_output_tokens=512,
            thinking_level="low",
        )
        print(f"[ok] {extract_model} replied: {reply!r}")
        if not reply:
            print("FAIL: empty reply from extract model")
            return 1
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1

    if not wav_path or not wav_path.is_file():
        print("\nNo WAV file provided - audio steps skipped.")
        print("Usage: python scripts/validate_gemini.py <audio.wav>")
        return 0

    print(f"\n--- Step 2: transcription ({wav_path.name}) ---")
    try:
        service = GeminiTranscribeService()
        wav_bytes = wav_path.read_bytes()
        transcript = client.transcribe_audio(
            wav_bytes,
            model=service.config.transcribe_model,
            mime_type="audio/wav",
            prompt=service.config.transcribe_prompt,
        ).strip()
        if not transcript:
            print("FAIL: empty transcript")
            return 1
        print(f"[ok] transcript:\n{transcript}")
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1

    print("\n--- Step 3: clinical extraction ---")
    try:
        raw = client.generate_text(
            prompt=build_extraction_prompt({}, transcript),
            model=service.config.extract_model,
            temperature=0,
            max_output_tokens=service.config.max_output_tokens,
            thinking_level=service.config.thinking_level,
            response_format=EXTRACTION_RESPONSE_FORMAT,
        )
        updates = validate_updates(parse_extraction_response(raw).get("updates", []))
        if not updates:
            print("FAIL: no valid updates extracted")
            return 1
        print("[ok] extracted updates:")
        for update in updates:
            print(f"    {update['field']} = {update['value']}")
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1

    print("\n--- Step 4: full service pipeline (simulated WebSocket) ---")
    try:
        service2 = GeminiTranscribeService(client=client)
        websocket = FakeWebSocket()
        import wave

        with wave.open(str(wav_path), "rb") as reader:
            pcm_bytes = reader.readframes(reader.getnframes())
        asyncio.run(service2._process_chunk(websocket, pcm_bytes, final=True))
        messages = websocket.sent
        transcripts = [m["text"] for m in messages if m["type"] == "content" and m["text"].startswith("Transcript")]
        field_updates = [m for m in messages if m["type"] == "update"]
        if not transcripts:
            print(f"FAIL: no transcript message. Sent messages: {json.dumps(messages, indent=2)[:2000]}")
            return 1
        print(transcripts[0])
        print(f"[ok] {len(field_updates)} field updates pushed to client:")
        for update in field_updates:
            print(f"    {update['field']} = {update['value']}")
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1

    print("\n" + "=" * 70)
    print("ALL CHECKS PASSED - Gemini 3.5 Transcribe integration is live-ready")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
