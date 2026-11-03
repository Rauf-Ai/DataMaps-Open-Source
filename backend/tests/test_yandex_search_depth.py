import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.maps import yandex_search


class YandexSearchDepthTest(unittest.TestCase):
    def test_parse_feature_extracts_region_and_city_from_address_components(self):
        feature = {
            "properties": {
                "name": "Клиника",
                "uri": "ymapsbm1://org?oid=1234567890",
                "CompanyMetaData": {
                    "id": "1234567890",
                    "address": "Москва, Неглинная улица, 10",
                    "Address": {
                        "Components": [
                            {"kind": "country", "name": "Россия"},
                            {"kind": "province", "name": "Центральный федеральный округ"},
                            {"kind": "province", "name": "Москва"},
                            {"kind": "locality", "name": "Москва"},
                        ]
                    },
                    "url": "clinic.example.com/contacts",
                    "Categories": [{"name": "Стоматология"}],
                },
            },
            "geometry": {"coordinates": [37.6, 55.7]},
        }

        row = yandex_search._parse_feature(feature)

        self.assertEqual(row["region"], "Москва")
        self.assertEqual(row["city"], "Москва")
        self.assertEqual(row["country"], "Россия")
        self.assertEqual(row["site"], "https://clinic.example.com/contacts")

    def test_parse_feature_does_not_treat_generic_metadata_link_as_site(self):
        feature = {
            "properties": {
                "name": "Кафе",
                "uri": "ymapsbm1://org?oid=123",
                "CompanyMetaData": {
                    "id": "123",
                    "Links": [
                        {"href": "https://example.com/path"},
                        {"href": "https://vk.ru/cafe"},
                    ],
                },
            },
            "geometry": {"coordinates": []},
        }

        row = yandex_search._parse_feature(feature)

        self.assertEqual(row["site"], "—")
        self.assertEqual(row["has_site"], "Нет")
        self.assertEqual(row["social"], "https://vk.ru/cafe")

    def test_extracts_localized_review_count_from_dynamic_yandex_markup(self):
        html = '<button aria-label="Рейтинг 4,8, 1 234 отзыва">Отзывы</button>'

        self.assertEqual(yandex_search._review_count_from_html(html), 1234)

    def test_jsonld_does_not_replace_organization_site_with_datamaps_url(self):
        company = {"site": "—", "has_site": "Нет", "reviews": 0, "rating": "—", "social": "—"}

        yandex_search._apply_jsonld(company, {"@type": "LocalBusiness", "url": "https://datamaps.ru/redirect"})

        self.assertEqual(company["site"], "—")
        self.assertEqual(company["has_site"], "Нет")

    def test_enrich_merges_vk_and_max_from_json_hydration_even_when_telegram_exists(self):
        company = {
            "oid": "123", "site": "—", "has_site": "Нет", "reviews": 0,
            "rating": "—", "social": "https://t.me/cafe", "services": "",
            "features": "", "price_range": "",
        }
        html = '<script>"href":"https:\\/\\/vk.ru\\/cafe","href":"https:\\/\\/max.ru\\/u\\/Cafe"</script>'

        with patch.object(
            yandex_search.SESSION,
            "get",
            return_value=SimpleNamespace(status_code=200, text=html),
        ):
            result = yandex_search._enrich(company)

        self.assertEqual(
            result["social"],
            "https://t.me/cafe, https://vk.ru/cafe, https://max.ru/u/Cafe",
        )

    def test_filtered_search_keeps_scanning_past_small_target_multiplier(self):
        def search_page(_query, skip):
            if skip >= 40:
                return []
            return [
                {
                    "oid": str(skip + index),
                    "name": f"Организация {skip + index}",
                    "address": f"Адрес {skip + index}",
                    "rating": 4.8,
                    "reviews": 20,
                    "has_site": "Нет",
                }
                for index in range(10)
            ]

        def target_filter(row):
            return int(row["oid"]) >= 20

        with (
            patch.object(yandex_search, "_search_page", side_effect=search_page),
            patch.object(yandex_search, "_parse_feature", side_effect=lambda row: row),
            patch.object(yandex_search, "_enrich", side_effect=lambda row, browser_session=None: row),
            patch.object(yandex_search, "_sleep"),
            patch.object(yandex_search.BrowserSession, "start", return_value=False),
            patch.object(yandex_search.BrowserSession, "stop"),
        ):
            rows = yandex_search.collect("стоматология Москва", 2, filter_fn=target_filter)

        self.assertEqual([row["oid"] for row in rows], ["20", "21"])

    def test_returns_partial_result_after_source_is_exhausted(self):
        def search_page(_query, skip):
            if skip == 0:
                return [
                    {"oid": "1", "name": "А", "address": "Адрес 1"},
                    {"oid": "2", "name": "Б", "address": "Адрес 2"},
                ]
            return []

        with (
            patch.object(yandex_search, "_search_page", side_effect=search_page),
            patch.object(yandex_search, "_parse_feature", side_effect=lambda row: row),
            patch.object(yandex_search, "_enrich", side_effect=lambda row, browser_session=None: row),
            patch.object(yandex_search, "_scrape_page", return_value=[]),
            patch.object(yandex_search, "_sleep"),
            patch.object(yandex_search.BrowserSession, "start", return_value=False),
            patch.object(yandex_search.BrowserSession, "stop"),
        ):
            rows = yandex_search.collect("стоматология Москва", 5)

        self.assertEqual([row["oid"] for row in rows], ["1", "2"])

    def test_emits_match_event_only_after_card_passes_filters(self):
        events = []
        cards = [
            {"oid": "1", "name": "Не подходит", "address": "А", "rating": 3.0},
            {"oid": "2", "name": "Подходит", "address": "Б", "rating": 4.9},
        ]

        with (
            patch.object(yandex_search, "_search_page", side_effect=[cards, [], [], []]),
            patch.object(yandex_search, "_parse_feature", side_effect=lambda row: row),
            patch.object(yandex_search, "_enrich", side_effect=lambda row, browser_session=None: row),
            patch.object(yandex_search, "_scrape_page", return_value=[]),
            patch.object(yandex_search, "_sleep"),
            patch.object(yandex_search.BrowserSession, "start", return_value=False),
            patch.object(yandex_search.BrowserSession, "stop"),
        ):
            rows = yandex_search.collect(
                "клиники Москва",
                2,
                emit=lambda event, **data: events.append((event, data)),
                filter_fn=lambda row: float(row.get("rating") or 0) >= 4,
            )

        matches = [data for event, data in events if event == "match"]
        self.assertEqual([row["name"] for row in rows], ["Подходит"])
        self.assertEqual([event["organization"]["name"] for event in matches], ["Подходит"])
        self.assertEqual(matches[0]["checked"], 2)


if __name__ == "__main__":
    unittest.main()
