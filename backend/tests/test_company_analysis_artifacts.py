import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openpyxl import load_workbook

from app.services.company_analysis_service import _build_artifacts
from app.services.company_analysis_xlsx import write_company_analysis_xlsx


class CompanyAnalysisArtifactsTest(unittest.TestCase):
    def test_services_sheet_keeps_service_name_and_price_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "analysis.xlsx"
            write_company_analysis_xlsx(
                path,
                {"companies": [{
                    "company": {"name": "Стомус"},
                    "services": [{"name": "Керамическая коронка", "price": "27 800 ₽"}],
                }]},
                options={"services_prices": True},
            )

            workbook = load_workbook(path)
            row = list(workbook["Услуги и цены"].iter_rows(min_row=2, values_only=True))[0]

        self.assertEqual(row, ("Стомус", "Услуга", "Керамическая коронка — 27 800 ₽"))

    def test_photos_only_archive_contains_only_selected_photos(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            photo = root / "photo.jpg"
            photo.write_bytes(b"jpeg")
            task = SimpleNamespace(id="photos-only", options_json=json.dumps({"photos": True}))
            result = {
                "companies": [{
                    "business_id": 7,
                    "company": {"name": "Фото студия"},
                    "photos": [str(photo)],
                    "reviews": [{"text": "Не должен попасть"}],
                    "services": ["Не должно попасть"],
                    "web": {"title": "Не должен попасть"},
                }],
            }
            with patch("app.services.company_analysis_service.settings.export_dir", tmp):
                _xlsx_path, zip_path = _build_artifacts(task, result, root)

            with zipfile.ZipFile(zip_path) as archive:
                self.assertEqual(archive.namelist(), ["companies/7/photos/photo.jpg"])

    def test_selected_structured_modules_are_exported_without_unselected_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = SimpleNamespace(
                id="reviews-services",
                options_json=json.dumps({"reviews": True, "services_prices": True}),
            )
            result = {
                "companies": [{
                    "business_id": 8,
                    "company": {"name": "Компания"},
                    "reviews": [{"rating": 5, "text": "Хорошо"}],
                    "services": ["Монтаж"],
                    "prices": ["1000 руб."],
                    "photos": ["/tmp/secret.jpg"],
                    "telegram": {"url": "https://t.me/not-selected"},
                }],
            }
            with patch("app.services.company_analysis_service.settings.export_dir", tmp):
                _xlsx_path, zip_path = _build_artifacts(task, result, root)

            with zipfile.ZipFile(zip_path) as archive:
                names = set(archive.namelist())
                self.assertEqual(names, {
                    "companies/8/reviews.json",
                    "companies/8/services_prices.json",
                })
                self.assertNotIn("result.json", names)
                self.assertNotIn("analysis.xlsx", names)

    def test_xlsx_writes_channel_profile_and_normalized_posts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "analysis.xlsx"
            write_company_analysis_xlsx(
                path,
                {"companies": [{
                    "company": {"name": "Клиника"},
                    "web": {"url": "https://clinic.test/", "pages": [{"url": "https://clinic.test/about", "title": "О клинике", "description": "История", "text": "Текст страницы"}]},
                    "telegram": {"url": "https://t.me/clinic", "title": "Клиника", "description": "Канал", "posts": [{"date": "2026-08-09", "text": "Пост Telegram", "media": [{"type": "photo", "title": "Фотография"}]}]},
                    "vk": {"url": "https://vk.com/clinic", "title": "Клиника VK", "description": "Группа", "posts": [{"date": "2026-08-08", "text": "Пост VK", "media": [{"type": "video", "title": "Обзор"}]}]},
                }]},
                options={"website": True, "telegram": True, "vk": True},
            )

            workbook = load_workbook(path)
            rows = list(workbook["Контент каналов"].iter_rows(min_row=2, values_only=True))

        self.assertIn(("Клиника", "Telegram", "Пост", "https://t.me/clinic", "Клиника", "Пост Telegram", "2026-08-09", "photo: Фотография"), rows)
        self.assertIn(("Клиника", "Сайт", "Страница", "https://clinic.test/about", "О клинике", "Текст страницы", None, None), rows)

    def test_archive_keeps_each_selected_normalized_source_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = SimpleNamespace(id="channel-json", options_json=json.dumps({"website": True, "telegram": True, "vk": True}))
            result = {"companies": [{
                "business_id": 9,
                "web": {"pages": [{"title": "Сайт"}]},
                "telegram": {"posts": [{"text": "TG"}]},
                "vk": {"posts": [{"text": "VK"}]},
            }]}
            with patch("app.services.company_analysis_service.settings.export_dir", tmp):
                _xlsx_path, zip_path = _build_artifacts(task, result, Path(tmp))

            with zipfile.ZipFile(zip_path) as archive:
                self.assertEqual(set(archive.namelist()), {
                    "companies/9/website.json",
                    "companies/9/telegram.json",
                    "companies/9/vk.json",
                })


if __name__ == "__main__":
    unittest.main()
