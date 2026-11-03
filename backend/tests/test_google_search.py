import unittest

from app.services.maps import google_search


class GoogleSearchTest(unittest.TestCase):
    def test_extracts_review_count_from_localized_google_labels(self):
        self.assertEqual(google_search._review_count_from_label("1,234 reviews"), 1234)
        self.assertEqual(google_search._review_count_from_label("1.234 ulasan"), 1234)
        self.assertEqual(google_search._review_count_from_label("1,234 条评价"), 1234)

    def test_builds_google_search_url_in_country_locale(self):
        self.assertEqual(
            google_search._google_maps_search_url("Restaurants Austin", "United States"),
            "https://www.google.com/maps/search/Restaurants%20Austin/?hl=en",
        )


if __name__ == "__main__":
    unittest.main()
