import unittest

from app.services.ai_filters_service import fallback_filters_from_prompt


class AiFiltersFallbackTest(unittest.TestCase):
    def test_restaurants_in_moscow_without_website(self):
        tree = [
            {
                "name": "Еда",
                "subcategories": [
                    {
                        "name": "Общественное питание",
                        "categories": ["Ресторан", "Кафе", "Столовая"],
                    }
                ],
            }
        ]

        result = fallback_filters_from_prompt(
            "Найди 10 ресторанов в Москве у кого много отзывов "
            "и с высоким рейтингом, но нет сайта",
            tree,
            ["Москва", "Московская область"],
        )

        filters = result["filters"]
        self.assertEqual(filters["categories"], ["Ресторан"])
        self.assertEqual(filters["cities"], ["Москва|Москва"])
        self.assertEqual(filters["has_website"], "no")
        self.assertGreaterEqual(filters["rating_min"], 4.0)
        self.assertGreaterEqual(filters["reviews_min"], 10)

    def test_live_query_is_not_limited_by_incomplete_local_category_tree(self):
        tree = [
            {
                "name": "Дом и ремонт",
                "subcategories": [
                    {"name": "Мебель", "categories": ["Мебель на заказ"]},
                    {"name": "Стройка", "categories": ["Аренда строительной и спецтехники"]},
                ],
            }
        ]

        result = fallback_filters_from_prompt(
            "Найди 10 ресторанов в Москве без сайта",
            tree,
            ["Москва и область"],
        )

        self.assertEqual(result["filters"]["categories"], ["Рестораны"])
        self.assertEqual(result["filters"]["cities"], ["Москва и область|Москва"])


if __name__ == "__main__":
    unittest.main()
