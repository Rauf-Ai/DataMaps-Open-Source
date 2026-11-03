import unittest

from app.services.business_identity import annotate_franchise_groups, deduplicate_cards, filter_franchises


class BusinessIdentityTest(unittest.TestCase):
    def test_merges_same_source_id_and_preserves_matched_queries(self):
        rows = [
            {"source": "Яндекс", "oid": "42", "name": "Кафе", "address": "Казань", "matched_query": "кафе"},
            {"source": "Яндекс", "oid": "42", "name": "Кафе", "address": "Казань", "phone": "+7 900 1", "matched_query": "рестораны"},
        ]

        result = deduplicate_cards(rows)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["phone"], "+7 900 1")
        self.assertEqual(result[0]["matched_queries"], ["кафе", "рестораны"])

    def test_keeps_same_brand_at_different_addresses_as_branches(self):
        rows = [
            {"source": "Яндекс", "oid": "1", "name": "Пятёрочка", "address": "ул. Ленина, 1"},
            {"source": "Яндекс", "oid": "2", "name": "Пятёрочка", "address": "ул. Мира, 2"},
        ]

        result = annotate_franchise_groups(deduplicate_cards(rows))

        self.assertEqual(len(result), 2)
        self.assertTrue(all(row["is_franchise"] for row in result))
        self.assertEqual({row["branch_count"] for row in result}, {2})
        self.assertEqual(len({row["chain_key"] for row in result}), 1)

    def test_franchise_filter_supports_all_modes(self):
        rows = annotate_franchise_groups([
            {"oid": "1", "name": "Сеть", "address": "А"},
            {"oid": "2", "name": "Сеть", "address": "Б"},
            {"oid": "3", "name": "Одиночная", "address": "В"},
        ])

        self.assertEqual(len(filter_franchises(rows, "any")), 3)
        self.assertEqual(len(filter_franchises(rows, "yes")), 2)
        self.assertEqual(len(filter_franchises(rows, "no")), 1)

    def test_explicit_chain_id_groups_name_variants(self):
        rows = annotate_franchise_groups([
            {"oid": "1", "name": "Coffee One Центр", "address": "А", "chain_id": "coffee-one"},
            {"oid": "2", "name": "Coffee One Экспресс", "address": "Б", "chain_id": "coffee-one"},
        ])

        self.assertEqual({row["chain_key"] for row in rows}, {"source:coffee-one"})
        self.assertEqual({row["branch_count"] for row in rows}, {2})

    def test_cross_source_same_phone_and_address_merges(self):
        rows = deduplicate_cards([
            {"source": "Яндекс", "oid": "ya-1", "name": "Клиника", "address": "Ленина, 1", "phone": "+7 900 123-45-67"},
            {"source": "Google", "oid": "g-1", "name": "Клиника здоровья", "address": "Ленина, 1", "phone": "8 (900) 123-45-67"},
        ])

        self.assertEqual(len(rows), 1)

    def test_shared_hotline_at_different_addresses_does_not_merge_branches(self):
        rows = deduplicate_cards([
            {"source": "Яндекс", "oid": "1", "name": "Сеть", "address": "Ленина, 1", "phone": "+7 800 100-00-00"},
            {"source": "Google", "oid": "2", "name": "Сеть", "address": "Мира, 2", "phone": "+7 800 100-00-00"},
        ])

        self.assertEqual(len(rows), 2)

    def test_same_name_and_nearby_coordinates_merges(self):
        rows = deduplicate_cards([
            {"source": "Яндекс", "oid": "1", "name": "Мастер", "lat": 55.7510, "lon": 37.6110},
            {"source": "Google", "oid": "2", "name": "Мастер", "lat": 55.7512, "lon": 37.6112},
        ])

        self.assertEqual(len(rows), 1)

    def test_same_domain_and_address_merges_similar_names(self):
        rows = deduplicate_cards([
            {"source": "Яндекс", "oid": "1", "name": "Медцентр Здоровье", "address": "Ленина, 1", "site": "health.example"},
            {"source": "Google", "oid": "2", "name": "Здоровье медцентр", "address": "Ленина, 1", "site": "https://www.health.example"},
        ])

        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
