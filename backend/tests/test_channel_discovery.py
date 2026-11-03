import unittest
from unittest.mock import patch

from app.services.deep_analysis.channel_discovery import (
    available_channel_ids,
    discover_company_channels,
)


class ChannelDiscoveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_marks_personal_social_links_and_keeps_card_website(self):
        async def personal_telegram(url):
            return {"status": "personal", "reason": "Личный Telegram, не анализируется", "url": url}

        async def personal_vk(url):
            return {"status": "personal", "reason": "Личный профиль VK, не анализируется", "url": url}

        with patch(
            "app.services.deep_analysis.channel_discovery.resolve_telegram_url",
            new=personal_telegram,
        ), patch(
            "app.services.deep_analysis.channel_discovery.resolve_vk_url",
            new=personal_vk,
        ):
            result = await discover_company_channels({
                "business_id": 7,
                "website": "clinic.example",
                "social": "https://t.me/alice, https://vk.com/id55",
            })

        self.assertEqual(result["website"], [{
            "business_id": 7,
            "key": "website",
            "label": "Сайт",
            "url": "https://clinic.example/",
            "status": "available",
            "reason": "",
        }])
        self.assertEqual(result["telegram"][0]["status"], "personal")
        self.assertEqual(result["vk"][0]["status"], "personal")


    def test_available_channel_ids_excludes_missing_and_personal_records(self):
        ids = available_channel_ids({
            "website": [{"business_id": 1, "status": "available"}],
            "telegram": [
                {"business_id": 1, "status": "personal"},
                {"business_id": 2, "status": "available"},
            ],
            "vk": [{"business_id": 1, "status": "missing"}],
        })

        self.assertEqual(ids, {"website": [1], "telegram": [2], "vk": []})


    async def test_discovery_rejects_telegram_invite_and_vk_message_without_platform_calls(self):
        with patch(
            "app.services.deep_analysis.channel_discovery.resolve_telegram_url",
            side_effect=AssertionError("resolver must not be called"),
        ), patch(
            "app.services.deep_analysis.channel_discovery.resolve_vk_url",
            side_effect=AssertionError("resolver must not be called"),
        ):
            result = await discover_company_channels({
                "business_id": 8,
                "social": "https://t.me/+abcdef, https://vk.com/im?sel=25",
            })

        self.assertEqual(result["telegram"][0]["status"], "private")
        self.assertEqual(result["vk"][0]["status"], "personal")
