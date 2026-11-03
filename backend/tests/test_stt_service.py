import json
import unittest
from unittest.mock import patch

import httpx
from app.services import stt_service


class _SttClient:
    timeout = None

    def __init__(self, *args, **kwargs):
        type(self).timeout = kwargs["timeout"]

    async def post(self, *args, **kwargs):
        return httpx.Response(200, json={"text": "Найди клиники в Казани"})

    async def aclose(self):
        return None


class SttServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_transcribe_audio_uses_openrouter_transcription_contract(self):
        captured = {}

        async def handler(request: httpx.Request):
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"text": "Найди клиники в Казани"})

        old = (stt_service.settings.llm_api_key, stt_service.settings.llm_base_url, stt_service.settings.stt_model)
        stt_service.settings.llm_api_key = "proxy-token"
        stt_service.settings.llm_base_url = "https://proxy.example/v1"
        stt_service.settings.stt_model = "openai/whisper-large-v3"
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                text = await stt_service.transcribe_audio(b"voice", "audio/webm;codecs=opus", client=client)
        finally:
            stt_service.settings.llm_api_key, stt_service.settings.llm_base_url, stt_service.settings.stt_model = old

        self.assertEqual(text, "Найди клиники в Казани")
        self.assertTrue(captured["url"].endswith("/v1/audio/transcriptions"))
        self.assertEqual(captured["body"]["model"], "openai/whisper-large-v3")
        self.assertEqual(captured["body"]["input_audio"]["format"], "webm")
        self.assertEqual(captured["body"]["language"], "ru")

    def test_audio_format_rejects_unsupported_input(self):
        with self.assertRaisesRegex(ValueError, "Поддерживаются"):
            stt_service.audio_format("video/quicktime")

    async def test_transcribe_audio_limits_provider_wait_to_one_minute(self):
        old = (stt_service.settings.llm_api_key, stt_service.settings.llm_timeout)
        stt_service.settings.llm_api_key = "proxy-token"
        stt_service.settings.llm_timeout = 120
        _SttClient.timeout = None
        try:
            with patch("app.services.stt_service.httpx.AsyncClient", _SttClient):
                await stt_service.transcribe_audio(b"voice", "audio/webm")
        finally:
            stt_service.settings.llm_api_key, stt_service.settings.llm_timeout = old

        self.assertEqual(_SttClient.timeout, 60)
