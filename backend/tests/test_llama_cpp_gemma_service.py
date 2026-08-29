import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import patch

from app.services.llama_cpp_gemma_service import (
    LlamaCppConfig,
    LlamaCppGemmaService,
    deduplicate_overlap,
    is_probably_silent,
    parse_partial_fields,
    parse_extraction_response,
    validate_updates,
)
from app.services.llama_server_manager import LlamaServerConfig, LlamaServerManager
from app.services.hardware_profiles import detect_profile


class ExtractionParsingTests(unittest.TestCase):
    def test_parses_plain_json_response(self):
        parsed = parse_extraction_response(
            json.dumps(
                {
                    "chief_complaint": "fever",
                    "spo2": 96,
                }
            )
        )

        self.assertEqual(parsed["updates"][0]["field"], "chief_complaint")
        self.assertEqual(parsed["updates"][1]["field"], "vitals.spo2")

    def test_recovers_json_from_wrapped_response(self):
        parsed = parse_extraction_response(
            'Here is the JSON:\n{"name":"Asha","blood_pressure":"118/76"}\nDone'
        )

        self.assertEqual(parsed["updates"][1]["field"], "vitals.blood_pressure")

    def test_empty_response_returns_no_extraction(self):
        self.assertEqual(parse_extraction_response(""), {"transcript": "", "updates": []})

    def test_non_json_response_returns_no_extraction(self):
        self.assertEqual(parse_extraction_response("not json"), {"transcript": "", "updates": []})

    def test_incremental_parser_waits_for_a_complete_field(self):
        self.assertEqual(parse_partial_fields('{"spo2": 9'), {})
        self.assertEqual(parse_partial_fields('{"spo2": 96}'), {"vitals.spo2": 96})

    def test_streaming_extraction_ignores_null_content_events(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def __iter__(self):
                return iter([
                    b'data: {"choices":[{"delta":{"content":null}}]}\n',
                    b'data: {"choices":[{"delta":{"content":"{\\\"name\\\":\\\"Asha\\\"}"}}]}\n',
                    b'data: [DONE]\n',
                ])

        emitted = []
        with patch("urllib.request.urlopen", return_value=FakeResponse()):
            LlamaCppGemmaService()._stream_extraction("my name is Asha", emitted.append)

        self.assertEqual(emitted, [{"name": "Asha"}])


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
        updates = validate_updates(
            [{"field": "symptoms", "value": "fever, cough, fatigue"}]
        )

        self.assertEqual(updates[0]["value"], ["fever", "cough", "fatigue"])

    def test_merges_duplicate_list_values(self):
        service = LlamaCppGemmaService()
        service.patient_state["symptoms"] = ["fever"]

        merged = service._merge_update("symptoms", ["Fever", "cough"])

        self.assertEqual(merged, ["fever", "cough"])

    def test_overwrites_scalar_values(self):
        service = LlamaCppGemmaService()
        service._merge_update("age", "42")

        self.assertEqual(service._merge_update("age", "43"), "43")

    def test_silent_pcm_is_filtered(self):
        self.assertTrue(is_probably_silent(b"\x00" * 320000, 180))

    def test_loud_pcm_is_not_filtered(self):
        self.assertFalse(is_probably_silent((1000).to_bytes(2, "little", signed=True) * 16000, 180))

    def test_removes_repeated_words_from_overlapping_audio_slices(self):
        self.assertEqual(
            deduplicate_overlap("my name is Prana", "Prana Kumar and I am 21"),
            "Kumar and I am 21",
        )


class VadSegmentationTests(unittest.IsolatedAsyncioTestCase):
    async def test_vad_processes_after_speech_then_silence(self):
        service = LlamaCppGemmaService(
            LlamaCppConfig(
                min_rms=180,
                vad_frame_ms=250,
                vad_start_ms=250,
                vad_end_silence_ms=500,
                min_speech_ms=250,
            )
        )
        service._enqueue_audio = AsyncMock()
        loud_frame = (1000).to_bytes(2, "little", signed=True) * 4000
        silent_frame = b"\x00" * 8000

        await service._handle_vad_frame(object(), loud_frame)
        await service._handle_vad_frame(object(), loud_frame)
        await service._handle_vad_frame(object(), silent_frame)
        await service._handle_vad_frame(object(), silent_frame)

        service._enqueue_audio.assert_awaited_once()

    async def test_vad_ignores_pure_silence(self):
        service = LlamaCppGemmaService()
        service._enqueue_audio = AsyncMock()
        silent_frame = b"\x00" * 8000

        await service._handle_vad_frame(object(), silent_frame)
        await service._handle_vad_frame(object(), silent_frame)

        service._enqueue_audio.assert_not_awaited()

    async def test_timed_slice_does_not_wait_for_silence(self):
        service = LlamaCppGemmaService(
            LlamaCppConfig(
                min_rms=180,
                vad_frame_ms=250,
                vad_start_ms=250,
                asr_slice_seconds=.5,
                max_speech_seconds=8,
                min_speech_ms=250,
            )
        )
        service._enqueue_audio = AsyncMock()
        loud_frame = (1000).to_bytes(2, "little", signed=True) * 4000

        await service._handle_vad_frame(object(), loud_frame)
        await service._handle_vad_frame(object(), loud_frame)

        service._enqueue_audio.assert_awaited_once()
        self.assertEqual(service._enqueue_audio.await_args.args[1], "timed")

    async def test_transcript_is_queued_for_extraction_at_punctuation(self):
        service = LlamaCppGemmaService()
        service._extraction_queue = asyncio.Queue()

        await service._commit_transcript("Patient has fever.", "timed", False)

        self.assertEqual(await service._extraction_queue.get(), "Patient has fever.")

    async def test_slow_extraction_does_not_block_later_transcription(self):
        class WebSocket:
            send_json = AsyncMock()

        service = LlamaCppGemmaService()
        transcripts = iter(["First statement.", "Second statement."])
        extraction_started, release_extraction = asyncio.Event(), asyncio.Event()
        service._transcribe = lambda _pcm: next(transcripts)

        async def block_extraction(_websocket, _text):
            extraction_started.set()
            await release_extraction.wait()

        service._stream_and_apply = block_extraction
        websocket = WebSocket()
        service._start_workers(websocket)
        assert service._audio_queue is not None
        await service._audio_queue.put((1, b"first", "timed", False))
        await service._audio_queue.put((2, b"second", "timed", False))
        try:
            await service._audio_queue.join()
            await extraction_started.wait()
            messages = [call.args[0]["text"] for call in websocket.send_json.await_args_list]
            self.assertIn("Transcript 1: First statement.\n", messages)
            self.assertIn("Transcript 2: Second statement.\n", messages)
        finally:
            release_extraction.set()
            await service._extraction_queue.join()
            await service._stop_workers()


class LlamaServerManagerTests(unittest.TestCase):
    def test_cpu_profile_disables_gpu_layer_offload(self):
        config = LlamaServerConfig(hardware_profile="cpu", binary_path=Path("C:/llama/llama-server.exe"))

        self.assertEqual(config.hardware_profile, "cpu")
        self.assertEqual(config.n_gpu_layers, 0)

    def test_custom_profile_keeps_explicit_binary(self):
        binary = Path("C:/custom/llama-server.exe")
        config = LlamaServerConfig(hardware_profile="custom", binary_path=binary)

        self.assertEqual(config.binary_path, binary)
        self.assertEqual(detect_profile("custom").name, "custom")

    def test_builds_backend_managed_server_command(self):
        config = LlamaServerConfig(
            binary_path=Path("C:/llama/llama-server.exe"),
            model_path=Path("C:/models/gemma.gguf"),
            ctx_size=2048,
            threads=4,
            host="127.0.0.1",
            port=8080,
        )
        command = LlamaServerManager(config)._build_command()

        self.assertIn("--ctx-size", command)
        self.assertIn("2048", command)
        self.assertIn("--no-webui", command)

    def test_downloads_missing_model_files(self):
        class FakeResponse:
            headers = {"Content-Length": "5"}

            def __enter__(self):
                self.chunks = [b"model", b""]
                return self

            def __exit__(self, *args):
                return False

            def read(self, _size):
                return self.chunks.pop(0)

        test_dir = Path(__file__).parent
        model_path = test_dir / "_tmp_model.gguf"
        mmproj_path = test_dir / "_tmp_mmproj.gguf"
        model_path.unlink(missing_ok=True)
        mmproj_path.unlink(missing_ok=True)
        try:
            config = LlamaServerConfig(
                model_path=model_path,
                mmproj_path=mmproj_path,
                model_url="https://example.test/model",
                mmproj_url="https://example.test/mmproj",
                download_models=True,
            )

            # Force the URL fallback: without this the HF Hub path would download
            # the real cached GGUF instead of exercising the mocked stream.
            with patch("huggingface_hub.hf_hub_download", side_effect=ImportError):
                with patch("urllib.request.urlopen", return_value=FakeResponse()):
                    LlamaServerManager(config)._ensure_model_files()

            self.assertEqual(model_path.read_bytes(), b"model")
            self.assertEqual(mmproj_path.read_bytes(), b"model")
        finally:
            model_path.unlink(missing_ok=True)
            mmproj_path.unlink(missing_ok=True)

    def test_spawn_env_prepends_llama_bin_to_path(self):
        config = LlamaServerConfig(binary_path=Path("C:/llama/bin/llama-server.exe"))
        env = LlamaServerManager(config)._build_env()

        self.assertTrue(env["PATH"].startswith("C:\\llama\\bin"))
        self.assertEqual(env["CUDA_MODULE_LOADING"], "LAZY")


if __name__ == "__main__":
    unittest.main()
