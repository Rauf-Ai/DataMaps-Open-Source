import unittest

from app.services.maps.normalization import absolute_external_url, locale_for_country, parse_count


class MapsNormalizationTest(unittest.TestCase):
    def test_makes_bare_organization_domain_an_absolute_url(self):
        self.assertEqual(absolute_external_url("restaurant.example.com/menu"), "https://restaurant.example.com/menu")

    def test_rejects_internal_datamaps_url_as_organization_site(self):
        self.assertEqual(absolute_external_url("https://datamaps.ru/company/42"), "")

    def test_parses_review_counts_in_supported_locales(self):
        self.assertEqual(parse_count("1 234 отзыва"), 1234)
        self.assertEqual(parse_count("1,234 reviews"), 1234)
        self.assertEqual(parse_count("1.234 ulasan"), 1234)
        self.assertEqual(parse_count("1,234 条评价"), 1234)

    def test_uses_primary_search_language_for_known_countries(self):
        self.assertEqual(locale_for_country("США"), ("en", "en-US"))
        self.assertEqual(locale_for_country("Индонезия"), ("id", "id-ID"))
        self.assertEqual(locale_for_country("Китай"), ("zh-CN", "zh-CN"))


if __name__ == "__main__":
    unittest.main()
