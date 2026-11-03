import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import company_analysis_service
from app.services.company_analysis_service import collect_company_batch, collect_company_target


class CompanyAnalysisServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_yandex_analysis_retains_saved_phone_and_address_when_detail_is_empty(self):
        target = {
            "business_id": 1,
            "source": "yandex",
            "name": "Стомус",
            "address": "Санкт-Петербург, Невский проспект, 10",
            "phone": "+7 (812) 555-12-34",
            "source_url": "https://yandex.ru/maps/org/1/",
        }
        collected = {
            "company": {"name": "Стомус", "address": "—", "phone": "—"},
            "services": [], "prices": [], "reviews": [], "photos": [],
            "news": [], "module_errors": {},
        }

        with patch(
            "app.services.company_analysis_service._collect_yandex",
            return_value=collected,
        ):
            row = await collect_company_target(
                target, {"reviews": False}, lambda *_args, **_kwargs: None,
                Path("/tmp"), None, None,
            )

        self.assertEqual(row["company"]["address"], "Санкт-Петербург, Невский проспект, 10")
        self.assertEqual(row["company"]["phone"], "+7 (812) 555-12-34")

    def test_normalizes_multiword_service_without_splitting_into_words(self):
        self.assertEqual(
            company_analysis_service.normalize_service_positions("Керамическая коронка — 27 800 ₽"),
            [{"name": "Керамическая коронка", "price": "27 800 ₽"}],
        )

    async def test_collects_eligible_channels_with_documented_limits(self):
        target = {
            "business_id": 1,
            "source": "yandex",
            "name": "Клиника",
            "website": "https://clinic.test",
            "social": "https://t.me/clinic, https://vk.com/clinic",
            "source_url": "https://yandex.ru/maps/org/1/",
        }
        collected = {"company": {"name": "Клиника", "site": "https://not-from-card.test"}, "services": [], "prices": [], "reviews": [], "photos": [], "news": [], "module_errors": {}}

        async def website(*_args, **_kwargs):
            return {"pages": []}

        async def telegram(*_args, **_kwargs):
            return {"posts": []}

        with patch("app.services.company_analysis_service._collect_yandex", return_value=collected), \
                patch("app.services.company_analysis_service.scrape_website", side_effect=website) as scrape_website, \
                patch("app.services.company_analysis_service.scrape_telegram", side_effect=telegram) as scrape_telegram, \
                patch("app.services.company_analysis_service.scrape_vk", return_value={"posts": []}) as scrape_vk:
            await collect_company_target(
                target,
                {"website": True, "telegram": True, "vk": True, "eligible_business_ids": {"website": [1], "telegram": [1], "vk": [1]}},
                lambda *_args, **_kwargs: None,
                Path("/tmp"), None, None,
            )

        self.assertEqual(scrape_website.call_args.kwargs["max_pages"], 5)
        self.assertEqual(scrape_website.call_args.args[0], "https://clinic.test")
        self.assertEqual(scrape_telegram.call_args.kwargs["max_messages"], 30)
        self.assertEqual(scrape_vk.call_args.kwargs["max_posts"], 30)

    async def test_collects_mixed_sources_and_counts_one_operation_per_success(self):
        targets = [
            {"business_id": 1, "name": "Яндекс А", "source": "yandex", "source_url": "https://yandex.ru/maps/org/1/"},
            {"business_id": 2, "name": "Google Б", "source": "google", "source_url": "https://google.com/maps/place/x"},
        ]

        def collector(target, _options, _emit):
            return {
                "business_id": target["business_id"],
                "source": target["source"],
                "company": {"name": target["name"]},
                "reviews": [],
                "photos": [],
                "services": [],
                "prices": [],
                "news": [],
                "web": {},
                "telegram": {},
                "vk": {},
                "module_errors": {},
            }

        result = await collect_company_batch(targets, {"photos": True}, collector)

        self.assertEqual(result["successful_count"], 2)
        self.assertEqual(result["billable_count"], 2)
        self.assertEqual({row["source"] for row in result["companies"]}, {"yandex", "google"})

    async def test_partial_module_failure_keeps_company_billable(self):
        def collector(target, _options, _emit):
            return {
                "business_id": target["business_id"],
                "source": "google",
                "company": {"name": "Компания"},
                "reviews": [],
                "photos": ["photo.jpg"],
                "services": [],
                "prices": [],
                "news": [],
                "web": {},
                "telegram": {},
                "vk": {},
                "module_errors": {"reviews": "Источник временно недоступен"},
            }

        result = await collect_company_batch(
            [{"business_id": 1, "source": "google", "source_url": "https://google.com/maps/place/x"}],
            {"photos": True, "reviews": True},
            collector,
        )

        self.assertEqual(result["partial_count"], 1)
        self.assertEqual(result["billable_count"], 1)

    async def test_total_empty_result_is_not_billable(self):
        def collector(_target, _options, _emit):
            return {"company": {}, "module_errors": {"base": "blocked"}}

        result = await collect_company_batch(
            [{"business_id": 1, "source": "yandex", "source_url": "https://yandex.ru/maps/org/1/"}],
            {"photos": True},
            collector,
        )

        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["billable_count"], 0)

    async def test_cached_identity_does_not_make_failed_source_billable(self):
        def collector(_target, _options, _emit):
            return {
                "company": {"name": "Имя из старого результата"},
                "_source_collected": False,
                "module_errors": {"base": "Источник не ответил"},
            }

        result = await collect_company_batch(
            [{"business_id": 1, "source": "google", "name": "Имя из старого результата"}],
            {"reviews": True},
            collector,
        )

        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["billable_count"], 0)


if __name__ == "__main__":
    unittest.main()
