import unittest

from app.services.ai_filters_service import fallback_filters_from_prompt
from app.services.chat_search_service import (
    default_live_plan,
    directions_label,
    merge_live_plan,
    normalize_live_ai_response,
)


class ChatSearchServiceTest(unittest.TestCase):
    def test_fallback_and_live_plan_recognize_cyrillic_max(self):
        fallback = fallback_filters_from_prompt("Нужны компании с профилем Макс", [], [])
        previous = {"searches": [{"query": "Кафе", "location": "Москва"}]}
        plan = merge_live_plan(
            {"filters": {"has_max": "yes"}},
            previous,
            "Нужны компании с Макс",
        )

        self.assertEqual(fallback["filters"]["has_max"], "yes")
        self.assertEqual(plan["social_nets"], ["max"])

    def test_normalized_ai_plan_adds_social_network_without_dropping_existing_one(self):
        previous = {
            "searches": [{"query": "Кафе", "location": "Москва"}],
            "social_nets": ["vk"],
        }
        raw = {
            "searches": [{"query": "Кафе", "location": "Москва"}],
            "social_nets": ["max"],
        }

        added = normalize_live_ai_response(raw, previous, "Добавь Max")
        removed = normalize_live_ai_response(raw, previous, "Добавь Max, убери VK")
        without_site = normalize_live_ai_response(raw, previous, "Добавь Max без сайта")
        rating_only = normalize_live_ai_response(raw, previous, "Добавь Max, только с рейтингом 4.5")

        self.assertEqual(added["social_nets"], ["max", "vk"])
        self.assertEqual(removed["social_nets"], ["max"])
        self.assertEqual(without_site["social_nets"], ["max", "vk"])
        self.assertEqual(rating_only["social_nets"], ["max", "vk"])

    def test_normalized_ai_plan_removes_only_the_named_social_network(self):
        previous = {
            "searches": [{"query": "Кафе", "location": "Москва"}],
            "social_nets": ["vk", "telegram"],
        }
        raw = {
            "searches": [{"query": "Кафе", "location": "Москва"}],
            "social_nets": ["max"],
        }

        for imperative in ("убери", "удали", "исключи", "без"):
            with self.subTest(imperative=imperative):
                plan = normalize_live_ai_response(
                    raw,
                    previous,
                    f"{imperative.capitalize()} VK",
                )

                self.assertEqual(plan["social_nets"], ["max", "telegram"])

    def test_normalized_ai_plan_only_named_social_replaces_raw_and_previous_socials(self):
        previous = {
            "searches": [{"query": "Кафе", "location": "Москва"}],
            "social_nets": ["vk", "telegram"],
        }
        raw = {
            "searches": [{"query": "Кафе", "location": "Москва"}],
            "social_nets": ["max"],
        }

        single = normalize_live_ai_response(raw, previous, "Добавь Max, только VK")
        multiple = normalize_live_ai_response(
            raw,
            previous,
            "Добавь Max, только VK и Telegram",
        )

        self.assertEqual(single["social_nets"], ["vk"])
        self.assertEqual(multiple["social_nets"], ["telegram", "vk"])

    def test_normalized_ai_plan_applies_removal_without_raw_social_list(self):
        previous = {
            "searches": [{"query": "Кафе", "location": "Москва"}],
            "social_nets": ["vk", "telegram"],
        }
        raw_searches = [{"query": "Кафе", "location": "Москва"}]

        for raw in (
            {"searches": raw_searches},
            {"searches": raw_searches, "social_nets": "max"},
        ):
            with self.subTest(raw_socials=raw.get("social_nets", "absent")):
                plan = normalize_live_ai_response(raw, previous, "Убери VK")

                self.assertEqual(plan["social_nets"], ["telegram"])

    def test_merge_live_plan_removal_overrides_contradictory_ai_value(self):
        previous = {
            "searches": [{"query": "Кафе", "location": "Москва"}],
            "social_nets": ["vk", "telegram"],
        }

        plan = merge_live_plan(
            {"filters": {"has_vk": "yes"}},
            previous,
            "Убери VK",
        )

        self.assertEqual(plan["social_nets"], ["telegram"])

    def test_merge_live_plan_adds_max_filter_when_the_latest_message_requests_max(self):
        plan = merge_live_plan(
            {"filters": {"has_max": "yes"}},
            default_live_plan(),
            "Нужны компании с Max",
        )

        self.assertEqual(plan["social_nets"], ["max"])

    def test_directions_label_uses_russian_plural_rules(self):
        self.assertEqual(directions_label(1), "1 поисковое направление")
        self.assertEqual(directions_label(2), "2 поисковых направления")
        self.assertEqual(directions_label(5), "5 поисковых направлений")
        self.assertEqual(directions_label(21), "21 поисковое направление")

    def test_follow_up_keeps_category_and_changes_location(self):
        previous = {
            "searches": [{"query": "Столовые", "location": "Татарстан"}],
            "source": "yandex",
            "max": 50,
            "f_site": "no",
            "f_phone": "any",
            "f_franchise": "any",
            "social_nets": [],
            "reviews_min": 0,
            "reviews_max": None,
            "rating_min": 0,
            "rating_max": 5,
        }
        result = {
            "filters": {
                "categories": [],
                "regions": [],
                "cities": ["Республика Татарстан|Казань"],
            }
        }

        plan = merge_live_plan(result, previous, "Оставь только Казань")

        self.assertEqual(plan["searches"], [{"query": "Столовые", "location": "Казань"}])
        self.assertEqual(plan["f_site"], "no")

    def test_new_contact_filter_overrides_previous_value(self):
        result = {
            "filters": {
                "categories": ["Кухни на заказ"],
                "regions": ["Республика Татарстан"],
                "has_website": "no",
            }
        }

        plan = merge_live_plan(result, {}, "Найди кухни без сайта в Татарстане")

        self.assertEqual(plan["f_site"], "no")
        self.assertEqual(
            plan["searches"][0],
            {"query": "Кухни на заказ", "location": "Республика Татарстан"},
        )

    def test_follow_up_can_remove_existing_contact_requirement(self):
        previous = {
            "searches": [{"query": "Кухни", "location": "Казань"}],
            "f_site": "no",
        }
        result = {"filters": {"has_website": "any"}}

        plan = merge_live_plan(result, previous, "Сайт теперь не важен")

        self.assertEqual(plan["f_site"], "any")

    def test_explicit_result_count_updates_plan_limit(self):
        result = {
            "filters": {
                "categories": ["Ресторан"],
                "cities": ["Москва|Москва"],
                "has_website": "no",
            }
        }

        plan = merge_live_plan(
            result,
            {},
            "Найди 10 ресторанов в Москве с высоким рейтингом, но без сайта",
        )

        self.assertEqual(plan["max"], 10)

    def test_explicit_result_count_supports_final_targets_above_parser_page_size(self):
        plan = merge_live_plan(
            {"filters": {"categories": ["Ресторан"], "cities": ["Москва|Москва"]}},
            {},
            "Найди 500 ресторанов в Москве",
        )

        self.assertEqual(plan["max"], 500)


if __name__ == "__main__":
    unittest.main()
