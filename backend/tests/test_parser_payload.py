import unittest

from app.routers.parser import _parse_search_payload


class ParserPayloadTest(unittest.TestCase):
    def test_preserves_source_per_search_and_final_target_count(self):
        source, target_count, params = _parse_search_payload({
            "source": "yandex",
            "target_count": 25,
            "searches": [
                {"query": "Кафе", "location": "Ташкент", "country": "Узбекистан", "source": "yandex"},
                {"query": "Restaurants", "location": "Berlin", "country": "Germany", "source": "google"},
            ],
        })

        self.assertEqual(source, "yandex")
        self.assertEqual(target_count, 25)
        self.assertEqual(params["target_count"], 25)
        self.assertEqual(params["searches"][1]["source"], "google")


if __name__ == "__main__":
    unittest.main()
