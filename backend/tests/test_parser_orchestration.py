import unittest
from unittest.mock import patch

from app.services.maps_service import _collect_jobs


class ParserOrchestrationTest(unittest.TestCase):
    def test_does_not_append_location_when_query_already_contains_it(self):
        jobs = [{"query": "стоматология Москва", "location": "Москва", "source": "yandex"}]

        with patch("app.services.maps_service._collect", return_value=[]) as collect:
            _collect_jobs(jobs, 5, lambda **_kw: None, None, "any")

        self.assertEqual(collect.call_args.args[1], "стоматология Москва")

    def test_uses_search_location_when_provider_row_has_no_geography(self):
        jobs = [{"query": "Restaurants", "location": "Dubai", "country": "UAE", "source": "google"}]
        response = [{"oid": "g1", "name": "A", "address": "Sheikh Zayed Road"}]

        with (
            patch("app.services.maps_service._collect", return_value=response),
            patch("app.services.maps_service.resolve_location", return_value={
                "country": "United Arab Emirates", "region": "Dubai", "city": "Dubai",
            }),
        ):
            rows = _collect_jobs(jobs, 1, lambda **_kw: None, None, "any")

        self.assertEqual(rows[0]["city"], "Dubai")
        self.assertEqual(rows[0]["region"], "Dubai")
        self.assertEqual(rows[0]["country"], "United Arab Emirates")
        self.assertIn("parsed_at", rows[0])

    def test_keeps_provider_geography_over_job_fallback(self):
        jobs = [{"query": "Restaurants", "location": "Berlin", "country": "Germany", "source": "google"}]
        response = [{
            "oid": "g1", "name": "A", "address": "Mitte", "city": "Berlin",
            "region": "Berlin", "country": "Germany",
        }]

        with (
            patch("app.services.maps_service._collect", return_value=response),
            patch("app.services.maps_service.resolve_location", return_value={
                "country": "Germany", "region": "Brandenburg", "city": "Berlin",
            }),
        ):
            rows = _collect_jobs(jobs, 1, lambda **_kw: None, None, "any")

        self.assertEqual(rows[0]["country"], "Germany")
        self.assertEqual(rows[0]["region"], "Berlin")

    def test_forwards_real_matched_organizations_in_progress(self):
        jobs = [{"query": "Клиника", "location": "Москва", "source": "yandex"}]
        events = []

        def fake_collect(_source, _query, _limit, emit, _filter, **_kwargs):
            emit(
                "match",
                found=1,
                checked=7,
                organization={"oid": "1", "name": "Клиника А", "rating": 4.8},
            )
            return [{"oid": "1", "name": "Клиника А", "address": "Москва"}]

        with patch("app.services.maps_service._collect", side_effect=fake_collect):
            _collect_jobs(jobs, 3, lambda **event: events.append(event), None, "any")

        live = next(event for event in events if event.get("recent_organizations"))
        self.assertEqual(live["checked_count"], 7)
        self.assertEqual(live["recent_organizations"][0]["name"], "Клиника А")
        self.assertEqual(live["recent_organizations"][0]["city"], "Москва")
        self.assertEqual(live["organization"]["oid"], "1")
        self.assertEqual(live["organization"]["matched_query"], "Клиника")

    def test_uses_each_rows_source_and_deduplicates_to_final_target(self):
        jobs = [
            {"query": "Кафе", "location": "Москва", "source": "yandex"},
            {"query": "Рестораны", "location": "Москва", "source": "yandex"},
            {"query": "Restaurants", "location": "Berlin", "source": "google"},
        ]
        responses = [
            [{"source": "Яндекс", "oid": "1", "name": "A", "address": "Москва, 1"}],
            [
                {"source": "Яндекс", "oid": "1", "name": "A", "address": "Москва, 1", "phone": "+7 900"},
                {"source": "Яндекс", "oid": "2", "name": "B", "address": "Москва, 2"},
            ],
            [{"source": "Google", "oid": "3", "name": "C", "address": "Berlin, 3"}],
        ]

        with patch("app.services.maps_service._collect", side_effect=responses) as collect:
            rows = _collect_jobs(jobs, 2, lambda **_kw: None, None, "any")

        self.assertEqual(len(rows), 2)
        self.assertEqual([call.args[0] for call in collect.call_args_list], ["yandex", "yandex"])
        self.assertEqual(rows[0]["matched_queries"], ["Кафе", "Рестораны"])

    def test_franchise_filter_runs_after_group_annotation(self):
        jobs = [{"query": "Кофейни", "location": "Казань", "source": "yandex"}]
        response = [[
            {"oid": "1", "name": "Сеть кофе", "address": "А"},
            {"oid": "2", "name": "Сеть кофе", "address": "Б"},
            {"oid": "3", "name": "Одиночная", "address": "В"},
        ]]

        with patch("app.services.maps_service._collect", side_effect=response):
            rows = _collect_jobs(jobs, 10, lambda **_kw: None, None, "yes")

        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["is_franchise"] for row in rows))


if __name__ == "__main__":
    unittest.main()
