import base64
import json
import unittest
import urllib.error
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.gemini_client import GeminiAPIError, GeminiClient, extract_text
from app.services.gemini_transcribe_service import (
    GeminiTranscribeConfig,
    GeminiTranscribeService,
    build_extraction_prompt,
    is_probably_silent,
    parse_extraction_response,
    validate_updates,
)


def make_config(**overrides) -> GeminiTranscribeConfig:
    defaults = dict(
        api_key="test-key",
        min_rms=180,
        vad_frame_ms=250,
        vad_start_ms=300,
        vad_end_silence_ms=900,
        max_speech_seconds=5,
        min_speech_ms=700,
    )
    defaults.update(overrides)
    return GeminiTranscribeConfig(**defaults)


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


class ExtractionParsingTests(unittest.TestCase):
    def test_parses_plain_json_response(self):
        parsed = parse_extraction_response(json.dumps({"updates": [{"field": "chief_complaint", "value": "fever"}]}))

        self.assertEqual(parsed["updates"][0]["field"], "chief_complaint")

    def test_recovers_json_from_wrapped_response(self):
        parsed = parse_extraction_response('Here is the JSON:\n{"updates":[]}\nDone')

        self.assertEqual(parsed["updates"], [])

    def test_empty_response_returns_no_extraction(self):
        self.assertEqual(parse_extraction_response(""), {"transcript": "", "updates": []})

    def test_non_json_response_returns_no_extraction(self):
        self.assertEqual(parse_extraction_response("not json"), {"transcript": "", "updates": []})

    def test_strips_thinking_blocks_before_parsing(self):
        parsed = parse_extraction_response('<think>private scratchpad</think>{"updates":[]}')

        self.assertEqual(parsed["updates"], [])

    def test_invalid_json_slice_returns_no_extraction(self):
        parsed = parse_extraction_response('{"updates": [{"field": "name" "value": "x"}]}')

        self.assertEqual(parsed, {"transcript": "", "updates": []})


class UpdateValidationTests(unittest.TestCase):
    def test_validates_scalar_and_nested_vitals(self):
        updates = validate_updates(
            [
                {"field": "name", "value": "Asha"},
                {"field": "vitals.spo2", "value": "96"},
                {"field": "unsupported", "value": "ignored"},
            ]
        )

        self.assertEqual(
            updates,
            [
                {"field": "name", "value": "Asha"},
                {"field": "vitals.spo2", "value": "96"},
            ],
        )

    def test_splits_list_field_string(self):
        updates = validate_updates([{"field": "symptoms", "value": "fever, cough, fatigue"}])

        self.assertEqual(updates[0]["value"], ["fever", "cough", "fatigue"])

    def test_merges_duplicate_list_values(self):
        service = GeminiTranscribeService(make_config(), client=MagicMock())
        service.patient_state["symptoms"] = ["fever"]

        merged = service._merge_update("symptoms", ["Fever", "cough"])

        self.assertEqual(merged, ["fever", "cough"])

    def test_overwrites_scalar_values(self):
        service = GeminiTranscribeService(make_config(), client=MagicMock())
        service._merge_update("age", "42")

        self.assertEqual(service._merge_update("age", "43"), "43")

    def test_silent_pcm_is_filtered(self):
        self.assertTrue(is_probably_silent(b"\x00" * 320000, 180))

    def test_loud_pcm_is_not_filtered(self):
        self.assertFalse(is_probably_silent((1000).to_bytes(2, "little", signed=True) * 16000, 180))


class VadSegmentationTests(unittest.IsolatedAsyncioTestCase):
    async def test_vad_processes_after_speech_then_silence(self):
        service = GeminiTranscribeService(
            make_config(
                min_rms=180,
                vad_frame_ms=250,
                vad_start_ms=250,
                vad_end_silence_ms=500,
                min_speech_ms=250,
            ),
            client=MagicMock(),
        )
        service._process_chunk = AsyncMock()
        websocket = object()
        loud_frame = (1000).to_bytes(2, "little", signed=True) * 4000
        silent_frame = b"\x00" * 8000

        await service._handle_vad_frame(websocket, loud_frame)
        await service._handle_vad_frame(websocket, loud_frame)
        await service._handle_vad_frame(websocket, silent_frame)
        await service._handle_vad_frame(websocket, silent_frame)

        service._process_chunk.assert_awaited_once()

    async def test_vad_ignores_pure_silence(self):
        service = GeminiTranscribeService(make_config(), client=MagicMock())
        service._process_chunk = AsyncMock()
        websocket = object()
        silent_frame = b"\x00" * 8000

        await service._handle_vad_frame(websocket, silent_frame)
        await service._handle_vad_frame(websocket, silent_frame)

        service._process_chunk.assert_not_awaited()


class GeminiClientTests(unittest.TestCase):
    def _capture_post(self, response_body):
        captured = {}

        class FakeResponse:
            def __init__(self, body):
                self._body = json.dumps(body).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return self._body

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["api_key_header"] = request.get_header("X-goog-api-key")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse(response_body)

        return captured, fake_urlopen

    def test_sends_api_key_header_and_payload(self):
        captured, fake_urlopen = self._capture_post({"status": "completed", "output_text": "ok"})
        client = GeminiClient(api_key="key-123", max_retries=1)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client.create_interaction(
                model="gemini-3.5-transcribe",
                input_blocks=[{"type": "text", "text": "hi"}],
            )

        self.assertIn("/interactions", captured["url"])
        self.assertEqual(captured["api_key_header"], "key-123")
        self.assertEqual(captured["body"]["model"], "gemini-3.5-transcribe")
        self.assertEqual(captured["body"]["input"][0]["type"], "text")

    def test_transcribe_audio_encodes_wav_base64(self):
        captured, fake_urlopen = self._capture_post({"output_text": "hello"})
        client = GeminiClient(api_key="key-123", max_retries=1)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = client.transcribe_audio(b"RIFF", model="gemini-3.5-transcribe")

        self.assertEqual(result, "hello")
        audio_block = captured["body"]["input"][0]
        self.assertEqual(audio_block["type"], "audio")
        self.assertEqual(audio_block["mime_type"], "audio/wav")
        self.assertEqual(audio_block["data"], base64.b64encode(b"RIFF").decode("utf-8"))

    def test_custom_vocabulary_is_sent_as_instruction(self):
        captured, fake_urlopen = self._capture_post({"output_text": "x"})
        client = GeminiClient(api_key="key-123", max_retries=1)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client.transcribe_audio(
                b"RIFF",
                model="gemini-3.5-transcribe",
                prompt="Transcribe this audio.",
                custom_vocabulary="paracetamol, SpO2",
            )

        text_block = next(b for b in captured["body"]["input"] if b["type"] == "text")
        self.assertIn("paracetamol", text_block["text"])

    def test_retries_on_429_then_succeeds(self):
        captured, fake_urlopen = self._capture_post({"output_text": "ok"})
        client = GeminiClient(api_key="key-123", max_retries=3)
        calls = {"count": 0}

        def flaky_urlopen(request, timeout=None):
            calls["count"] += 1
            if calls["count"] == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", hdrs=None, fp=None)
            return fake_urlopen(request, timeout=timeout)

        with patch("urllib.request.urlopen", side_effect=flaky_urlopen):
            with patch("time.sleep"):
                result = client.generate_text(prompt="hi", model="gemini-3.5-flash")

        self.assertEqual(result, "ok")
        self.assertEqual(calls["count"], 2)

    def test_does_not_retry_on_400(self):
        client = GeminiClient(api_key="key-123", max_retries=3)
        calls = {"count": 0}

        def bad_request_urlopen(request, timeout=None):
            calls["count"] += 1
            raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", hdrs=None, fp=None)

        with patch("urllib.request.urlopen", side_effect=bad_request_urlopen):
            with self.assertRaises(GeminiAPIError) as ctx:
                client.generate_text(prompt="hi", model="gemini-3.5-flash")

        self.assertEqual(calls["count"], 1)
        self.assertIn("400", str(ctx.exception))

    def test_missing_api_key_raises_informative_error(self):
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("GEMINI_API_KEY", None)
            with self.assertRaises(GeminiAPIError) as ctx:
                GeminiClient(api_key="")

            self.assertIn("GEMINI_API_KEY", str(ctx.exception))

    def test_extract_text_prefers_output_text(self):
        interaction = {
            "output_text": "  polished  ",
            "steps": [{"type": "model_output", "content": [{"type": "text", "text": "raw"}]}],
        }
        self.assertEqual(extract_text(interaction), "polished")

    def test_extract_text_falls_back_to_steps(self):
        interaction = {
            "steps": [
                {"type": "thought", "content": []},
                {
                    "type": "model_output",
                    "content": [{"type": "text", "text": "line1"}, {"type": "text", "text": "line2"}],
                },
            ]
        }
        self.assertEqual(extract_text(interaction), "line1\nline2")

    def test_extract_text_empty_interaction(self):
        self.assertEqual(extract_text({}), "")

    def test_incomplete_status_raises(self):
        captured, fake_urlopen = self._capture_post({"status": "incomplete"})
        client = GeminiClient(api_key="key-123", max_retries=1)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(GeminiAPIError) as ctx:
                client.generate_text(prompt="hi", model="gemini-3.5-flash")

        self.assertIn("incomplete", str(ctx.exception))

    def test_generate_text_passes_thinking_level_and_response_format(self):
        captured, fake_urlopen = self._capture_post({"status": "completed", "output_text": "ok"})
        client = GeminiClient(api_key="key-123", max_retries=1)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client.generate_text(
                prompt="hi",
                model="gemini-3.5-flash",
                thinking_level="low",
                response_format={"type": "object"},
            )

        generation_config = captured["body"]["generation_config"]
        self.assertEqual(generation_config["thinking_level"], "low")
        self.assertEqual(captured["body"]["response_format"], {"type": "object"})


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def _make_service(self, transcript, extraction_json):
        client = MagicMock()
        client.transcribe_audio.return_value = transcript
        client.generate_text.return_value = json.dumps(extraction_json)
        service = GeminiTranscribeService(make_config(), client=client)
        return service, client

    async def test_process_chunk_sends_transcript_and_updates(self):
        service, client = self._make_service(
            "Patient Ramesh, 45 years old, has fever and cough",
            {"updates": [{"field": "name", "value": "Ramesh"}, {"field": "symptoms", "value": "fever, cough"}]},
        )
        websocket = FakeWebSocket()
        loud_pcm = (1000).to_bytes(2, "little", signed=True) * 40000  # 2.5s of loud audio

        await service._process_chunk(websocket, loud_pcm, final=False)

        types = [m["type"] for m in websocket.sent]
        self.assertIn("content", types)
        updates = [m for m in websocket.sent if m["type"] == "update"]
        self.assertEqual(updates[0]["field"], "name")
        self.assertEqual(updates[0]["value"], "Ramesh")
        self.assertEqual(updates[1]["field"], "symptoms")
        self.assertEqual(updates[1]["value"], ["fever", "cough"])

        self.assertEqual(client.transcribe_audio.call_count, 1)
        prompt = client.generate_text.call_args.kwargs["prompt"]
        self.assertIn("fever and cough", prompt)

    async def test_process_chunk_skips_silence(self):
        service, client = self._make_service("irrelevant", {"updates": []})
        websocket = FakeWebSocket()

        await service._process_chunk(websocket, b"\x00" * 320000, final=False)

        client.transcribe_audio.assert_not_called()
        self.assertTrue(any("Skipped silent" in m.get("text", "") for m in websocket.sent))

    async def test_run_pipeline_returns_empty_when_no_transcript(self):
        service, client = self._make_service("", {"updates": []})

        transcript, updates = service._run_pipeline((1000).to_bytes(2, "little", signed=True) * 40000)

        self.assertEqual(transcript, "")
        self.assertEqual(updates, [])
        client.generate_text.assert_not_called()

    async def test_process_chunk_reports_errors_gracefully(self):
        client = MagicMock()
        client.transcribe_audio.side_effect = GeminiAPIError("Gemini API HTTP 429: quota")
        service = GeminiTranscribeService(make_config(), client=client)
        websocket = FakeWebSocket()

        await service._process_chunk(websocket, (1000).to_bytes(2, "little", signed=True) * 40000, final=False)

        self.assertTrue(any("failed" in m.get("text", "") for m in websocket.sent))

    async def test_missing_api_key_sends_friendly_message(self):
        service = GeminiTranscribeService(make_config(api_key=""))
        websocket = FakeWebSocket()

        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("GEMINI_API_KEY", None)
            await service.handle_session(websocket)

        self.assertEqual(len(websocket.sent), 1)
        self.assertIn("GEMINI_API_KEY", websocket.sent[0]["text"])


class PromptTests(unittest.TestCase):
    def test_prompt_includes_transcript_and_known_state(self):
        prompt = build_extraction_prompt({"name": "Asha"}, "Patient has fever since two days")

        self.assertIn("fever since two days", prompt)
        self.assertIn("Asha", prompt)
        self.assertIn("updates", prompt)


if __name__ == "__main__":
    unittest.main()
