import unittest
from unittest.mock import patch

from app.services import llm_client
from app.services.llm_client import _call_chat


class _Response:
    status_code = 200

    def json(self):
        return {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": '{"searches": ['},
                }
            ]
        }


class _Client:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        return _Response()


class _SuccessfulResponse:
    status_code = 200

    def json(self):
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"ok": true}'},
                }
            ]
        }


class _CapturingClient:
    payload = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        type(self).payload = kwargs["json"]
        return _SuccessfulResponse()


class LlmClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_reports_truncated_completion(self):
        with patch("app.services.llm_client.httpx.AsyncClient", _Client):
            parsed, error = await _call_chat({}, {}, "https://example.test", 10)

        self.assertIsNone(parsed)
        self.assertEqual(error, "LLM response truncated (finish_reason=length)")

    async def test_fast_request_disables_reasoning_before_requesting_json(self):
        settings = llm_client.get_settings()
        old = (settings.llm_api_key, settings.llm_base_url, settings.llm_model, settings.llm_timeout)
        settings.llm_api_key = "proxy-token"
        settings.llm_base_url = "https://proxy.example/v1"
        settings.llm_model = "google/gemini-2.5-flash-lite"
        settings.llm_timeout = 30
        _CapturingClient.payload = None
        try:
            with patch("app.services.llm_client.httpx.AsyncClient", _CapturingClient):
                parsed, error = await llm_client.chat_json(
                    "Return a JSON object.",
                    "Return {\"ok\": true}.",
                    fast=True,
                )
        finally:
            settings.llm_api_key, settings.llm_base_url, settings.llm_model, settings.llm_timeout = old

        self.assertEqual(parsed, {"ok": True})
        self.assertIsNone(error)
        self.assertEqual(_CapturingClient.payload["reasoning"], {"effort": "none", "exclude": True})
        self.assertNotIn("reasoning_effort", _CapturingClient.payload)


if __name__ == "__main__":
    unittest.main()
