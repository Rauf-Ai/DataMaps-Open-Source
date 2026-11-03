import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Business
from app.services.live_index_service import upsert_business_from_parser_row
from app.services.maps.xlsx import write_companies_xlsx


class LiveIndexFranchiseTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_same_brand_domain_with_different_source_ids_keeps_branches(self):
        with Session(self.engine) as db:
            upsert_business_from_parser_row(db, {
                "source": "Яндекс", "oid": "1", "name": "Сеть", "address": "Улица 1",
                "site": "https://chain.example", "brand_name": "Сеть", "chain_key": "name:сеть",
                "is_franchise": True, "branch_count": 2,
            })
            upsert_business_from_parser_row(db, {
                "source": "Яндекс", "oid": "2", "name": "Сеть", "address": "Улица 2",
                "site": "https://chain.example", "brand_name": "Сеть", "chain_key": "name:сеть",
                "is_franchise": True, "branch_count": 2,
            })
            db.commit()
            rows = db.execute(select(Business).order_by(Business.id)).scalars().all()

        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0].is_franchise)
        self.assertEqual(rows[0].branch_count, 2)

    def test_xlsx_contains_chain_and_query_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.xlsx"
            write_companies_xlsx(path, [{
                "name": "Сеть", "brand_name": "Сеть", "is_franchise": True,
                "branch_count": 2, "matched_queries": ["Кофейни", "Кофе с собой"],
            }])
            workbook = load_workbook(path, read_only=True)
            headers = [cell.value for cell in next(workbook["Компании"].iter_rows())]

        self.assertIn("Бренд", headers)
        self.assertIn("Франшиза", headers)
        self.assertIn("Количество точек", headers)
        self.assertIn("Найдено по запросам", headers)

    def test_shared_franchise_domain_does_not_merge_different_cross_source_addresses(self):
        with Session(self.engine) as db:
            upsert_business_from_parser_row(db, {
                "source": "Яндекс", "oid": "ya-1", "name": "Сеть", "address": "Улица 1",
                "site": "https://chain.example",
            })
            upsert_business_from_parser_row(db, {
                "source": "Google", "oid": "g-2", "name": "Сеть", "address": "Улица 2",
                "site": "https://chain.example",
            })
            db.commit()
            count = len(db.execute(select(Business)).scalars().all())

        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
