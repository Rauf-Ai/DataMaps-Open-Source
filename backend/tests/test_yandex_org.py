import unittest

from bs4 import BeautifulSoup

from app.services.maps import yandex_org


class YandexOrganizationExtractionTest(unittest.TestCase):
    def test_gallery_photo_extractor_excludes_page_wide_advertising_image(self):
        soup = BeautifulSoup(
            '<div data-testid="gallery"><img src="https://avatars.mds.yandex.net/get-biz/1/clinic-photo/XXL_height"></div>'
            '<aside><img src="https://avatars.mds.yandex.net/get-biz/2/king-and-jester-banner/XXL_height"></aside>',
            "html.parser",
        )

        photos = yandex_org.extract_gallery_photo_urls(soup, [])

        self.assertEqual(
            photos,
            ["https://avatars.mds.yandex.net/get-biz/1/clinic-photo/XXL_height"],
        )

    def test_service_extractor_uses_jsonld_offer_and_ignores_prose_price(self):
        soup = BeautifulSoup(
            "<p>С её помощью можно предотвратить убыль кости — 27 800 ₽</p>",
            "html.parser",
        )
        jsonld = [{
            "@type": "LocalBusiness",
            "makesOffer": [{
                "name": "Керамическая коронка",
                "price": "27800",
                "priceCurrency": "RUB",
            }],
        }]

        positions = yandex_org.extract_service_positions(soup, jsonld)

        self.assertEqual(
            positions,
            [{"name": "Керамическая коронка", "price": "27 800 ₽"}],
        )


if __name__ == "__main__":
    unittest.main()
