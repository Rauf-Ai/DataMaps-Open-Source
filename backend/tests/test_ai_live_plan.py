import unittest
from unittest.mock import AsyncMock, patch

from app.services.chat_search_service import _expand_search_groups, normalize_live_ai_response, refine_live_plan


class AiLivePlanTest(unittest.IsolatedAsyncioTestCase):
    def test_removes_location_from_query_field(self):
        raw = {
            "search_groups": [{
                "queries": ["стоматология Москва", "зубная клиника Москва", "лечение зубов Москва"],
                "locations": ["Москва"],
                "country": "Россия",
                "source": "yandex",
            }],
            "target_count": 5,
        }

        result = normalize_live_ai_response(raw, {}, "Найди 5 стоматологий в Москве")

        self.assertEqual([row["query"] for row in result["searches"]], ["стоматология"])
        self.assertTrue(all(row["location"] == "Москва" for row in result["searches"]))

    def test_removes_inflected_location_from_query_field(self):
        raw = {
            "search_groups": [{
                "queries": ["стоматологии в Москве", "зубные клиники Москвы", "лечение зубов Москва"],
                "locations": ["Москва"],
                "country": "Россия",
                "source": "yandex",
            }],
            "target_count": 50,
        }

        result = normalize_live_ai_response(raw, {}, "Найди стоматологии в Москве")

        self.assertEqual(
            [row["query"] for row in result["searches"]],
            ["стоматологии", "зубные клиники", "лечение зубов"],
        )
        self.assertTrue(all(row["location"] == "Москва" for row in result["searches"]))

    def test_expands_compact_search_groups_for_many_locations(self):
        raw = {
            "target_count": 20,
            "search_groups": [
                {
                    "queries": ["установка ГБО", "сервис ГБО", "газовое оборудование для авто"],
                    "locations": ["Москва", "Казань", "Новосибирск"],
                    "country": "Россия",
                    "source": "yandex",
                }
            ],
        }

        result = _expand_search_groups(raw["search_groups"])

        self.assertEqual(len(result), 9)
        self.assertEqual(
            {row["location"] for row in result},
            {"Москва", "Казань", "Новосибирск"},
        )
        self.assertTrue(all(row["source"] == "yandex" for row in result))

    def test_scales_search_rows_to_small_target_across_many_locations(self):
        locations = [
            "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
            "Нижний Новгород", "Красноярск", "Челябинск", "Самара", "Уфа",
            "Ростов-на-Дону", "Краснодар", "Омск", "Воронеж", "Пермь",
        ]
        raw = {
            "target_count": 20,
            "f_site": "no",
            "reviews_min": 50,
            "rating_min": 4.0,
            "search_groups": [{
                "queries": [
                    "установка ГБО", "магазин ГБО", "сервис ГБО",
                    "газобаллонное оборудование", "монтаж ГБО",
                ],
                "locations": locations,
                "country": "Россия",
                "source": "yandex",
            }],
        }

        result = normalize_live_ai_response(raw, {}, "Найди 20 установщиков ГБО по городам-миллионникам")

        self.assertEqual(len(result["searches"]), 15)
        self.assertEqual({row["location"] for row in result["searches"]}, set(locations))

    def test_routes_each_country_and_keeps_multiple_variants(self):
        raw = {
            "target_count": 30,
            "searches": [
                {"query": "Рестораны", "location": "Москва", "country": "Россия", "source": "google"},
                {"query": "Кафе и рестораны", "location": "Москва", "country": "Россия", "source": "yandex"},
                {"query": "Общественное питание", "location": "Москва", "country": "Россия", "source": "yandex"},
                {"query": "Restaurants", "location": "Berlin", "country": "Germany", "source": "yandex"},
                {"query": "Dining", "location": "Berlin", "country": "Germany", "source": "google"},
                {"query": "Food service", "location": "Berlin", "country": "Germany", "source": "google"},
            ],
            "reasoning": "Расширил формулировки.",
        }

        result = normalize_live_ai_response(raw, {}, "Найди рестораны в Москве и Берлине")

        self.assertEqual(result["target_count"], 30)
        self.assertEqual(
            {row["source"] for row in result["searches"] if row["location"] == "Москва"},
            {"yandex"},
        )
        self.assertEqual(
            {row["source"] for row in result["searches"] if row["location"] == "Berlin"},
            {"google"},
        )

    def test_uzbekistan_is_routed_to_yandex(self):
        raw = {
            "searches": [
                {"query": "Кафе", "location": "Ташкент", "country": "Узбекистан"},
                {"query": "Рестораны", "location": "Ташкент", "country": "Узбекистан"},
                {"query": "Общепит", "location": "Ташкент", "country": "Узбекистан"},
            ]
        }

        result = normalize_live_ai_response(raw, {}, "Найди кафе Ташкента")

        self.assertEqual({row["source"] for row in result["searches"]}, {"yandex"})

    def test_attaches_local_language_metadata_to_international_queries(self):
        raw = {
            "searches": [
                {"query": "Restaurants", "location": "Austin", "country": "United States"},
                {"query": "Restoran", "location": "Jakarta", "country": "Indonesia"},
                {"query": "餐厅", "location": "上海", "country": "China"},
            ]
        }

        result = normalize_live_ai_response(raw, {}, "Найди рестораны в США, Индонезии и Китае")
        by_location = {row["location"]: row for row in result["searches"]}

        self.assertEqual(by_location["Austin"]["language"], "en")
        self.assertEqual(by_location["Jakarta"]["language"], "id")
        self.assertEqual(by_location["上海"]["google_locale"], "zh-CN")

    async def test_technical_failure_uses_only_latest_message_for_fallback(self):
        messages = [
            {"role": "user", "content": "Системный старый запрос"},
            {"role": "assistant", "content": "Старый ответ"},
            {"role": "user", "content": "Найди стоматологии Казани"},
        ]
        fallback = {
            "searches": [
                {"query": "Стоматологии", "location": "Казань", "country": "Россия", "source": "yandex"}
            ],
            "target_count": 50,
            "reasoning": "Резервный разбор.",
        }
        with patch("app.services.chat_search_service.chat_json", new=AsyncMock(return_value=(None, "LLM timeout"))), \
                patch("app.services.chat_search_service.fallback_live_plan", return_value=fallback) as fallback_mock:
            result = await refine_live_plan(messages, {})

        fallback_mock.assert_called_once_with("Найди стоматологии Казани", {})
        self.assertTrue(result["fallback_used"])
        self.assertEqual(result["llm_error"], "LLM timeout")
        self.assertIn("резервный", result["assistant_message"].lower())

    async def test_incomplete_llm_plan_activates_fallback(self):
        raw = {
            "searches": [{"query": "Кафе", "location": "Казань", "country": "Россия"}],
            "target_count": 10,
        }
        fallback = {
            "searches": [{"query": "Кафе", "location": "Казань", "country": "Россия", "source": "yandex"}],
            "target_count": 10,
        }
        with patch("app.services.chat_search_service.chat_json", new=AsyncMock(return_value=(raw, None))), \
                patch("app.services.chat_search_service.fallback_live_plan", return_value=fallback):
            result = await refine_live_plan([{"role": "user", "content": "Найди кафе Казани"}], {})

        self.assertTrue(result["fallback_used"])
        self.assertIn("неполный", result["llm_error"])


if __name__ == "__main__":
    unittest.main()
