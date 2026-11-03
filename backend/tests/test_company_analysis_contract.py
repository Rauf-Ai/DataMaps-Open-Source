import unittest

from app.services.company_analysis_service import normalize_company_analysis_options


class CompanyAnalysisContractTest(unittest.TestCase):
    def test_normalizes_shared_options(self):
        result = normalize_company_analysis_options({
            "photos": True,
            "reviews": True,
            "review_count": 75,
            "review_rating_min": 2,
            "review_rating_max": 4,
            "services_prices": True,
            "website": True,
            "telegram": False,
            "vk": True,
        })

        self.assertEqual(result["review_count"], 75)
        self.assertEqual(result["review_rating_min"], 2)
        self.assertTrue(result["photos"])
        self.assertTrue(result["vk"])

    def test_rejects_empty_module_selection(self):
        with self.assertRaisesRegex(ValueError, "один модуль"):
            normalize_company_analysis_options({})

    def test_rejects_invalid_review_bounds(self):
        with self.assertRaisesRegex(ValueError, "рейтинга"):
            normalize_company_analysis_options({
                "reviews": True,
                "review_count": 50,
                "review_rating_min": 5,
                "review_rating_max": 2,
            })

    def test_rejects_review_count_above_limit(self):
        with self.assertRaisesRegex(ValueError, "500"):
            normalize_company_analysis_options({"reviews": True, "review_count": 501})


if __name__ == "__main__":
    unittest.main()
