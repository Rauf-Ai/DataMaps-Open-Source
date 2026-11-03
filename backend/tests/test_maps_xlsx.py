import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from app.services.maps.xlsx import write_companies_xlsx


class MapsXlsxTest(unittest.TestCase):
    def test_exports_country_region_freshness_and_direct_site_link(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "companies.xlsx"
            parsed_at = datetime(2026, 8, 3, 12, 34, 56)
            write_companies_xlsx(path, [{
                "name": "Bistro", "site": "https://bistro.example/menu",
                "country": "United States", "region": "California", "parsed_at": parsed_at,
            }])
            sheet = load_workbook(path).active

        headers = {cell.value: cell.column for cell in sheet[1]}
        self.assertIn("Страна", headers)
        self.assertIn("Регион", headers)
        self.assertIn("Актуальность", headers)
        site = sheet.cell(2, headers["Сайт"])
        self.assertEqual(site.hyperlink.target, "https://bistro.example/menu")
        self.assertEqual(sheet.cell(2, headers["Страна"]).value, "United States")
        self.assertEqual(sheet.cell(2, headers["Регион"]).value, "California")
        self.assertEqual(sheet.cell(2, headers["Актуальность"]).value, parsed_at)


if __name__ == "__main__":
    unittest.main()
