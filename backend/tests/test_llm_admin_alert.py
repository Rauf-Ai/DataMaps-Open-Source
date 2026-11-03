import unittest
from unittest.mock import AsyncMock, patch

from app.services.email_service import _llm_alert_cache, send_llm_failure_alert


class LlmAdminAlertTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _llm_alert_cache.clear()

    async def test_redacts_secrets_and_suppresses_duplicate_alert(self):
        error = "Authorization Bearer sk-secret-value failed for https://provider.test/v1"
        with patch("app.services.email_service.send_admin_alert", new=AsyncMock()) as send:
            first = await send_llm_failure_alert(error, "req-1", 17)
            second = await send_llm_failure_alert(error, "req-2", 18)

        self.assertTrue(first)
        self.assertFalse(second)
        body = send.await_args.args[1]
        self.assertNotIn("sk-secret-value", body)
        self.assertIn("req-1", body)


if __name__ == "__main__":
    unittest.main()
