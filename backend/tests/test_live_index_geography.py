import unittest

from app.services.live_index_service import _business_payload


class LiveIndexGeographyTest(unittest.TestCase):
    def test_keeps_country_and_absolute_organization_website(self):
        payload = _business_payload({
            "name": "Bistro", "country": "United States", "site": "bistro.example/menu",
        })

        self.assertEqual(payload["country"], "United States")
        self.assertEqual(payload["website"], "https://bistro.example/menu")


if __name__ == "__main__":
    unittest.main()
