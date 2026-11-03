"""
Импорт CSV-файлов с организациями в DuckDB.

Запуск:
    docker-compose run --rm backend python /scripts/import.py
или локально:
    python scripts/import.py --csv-dir ./data/csv --duckdb ./data/organizations.duckdb
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import duckdb
from tqdm import tqdm


# Канонические имена 38 колонок CSV в порядке появления.
CSV_COLS = [f"c{i}" for i in range(38)]


SCHEMA_SQL = """
CREATE TABLE organizations_new (
    id BIGINT,
    name TEXT,
    categories TEXT,
    source_category TEXT,
    postcode TEXT,
    country TEXT,
    region TEXT,
    district TEXT,
    city TEXT,
    address TEXT,
    phone_mobile TEXT,
    phone_landline TEXT,
    website TEXT,
    email TEXT,
    schedule TEXT,
    lat DOUBLE,
    lon DOUBLE,
    ratings_count INTEGER,
    reviews_count INTEGER,
    rating DOUBLE,
    network_id TEXT,
    network_name TEXT,
    attributes TEXT,
    vk TEXT,
    ok TEXT,
    facebook TEXT,
    instagram TEXT,
    youtube TEXT,
    twitter TEXT,
    telegram TEXT,
    viber TEXT,
    whatsapp TEXT,
    yandex_card_url TEXT,
    all_websites TEXT,
    all_phones TEXT,
    online_booking TEXT,
    order_url TEXT,
    priority_placement TEXT,
    logo TEXT,
    has_phone BOOLEAN,
    has_website BOOLEAN,
    has_email BOOLEAN,
    has_vk BOOLEAN,
    has_instagram BOOLEAN,
    has_telegram BOOLEAN
);
"""


INSERT_SQL = """
INSERT INTO organizations_new (
    id, name, categories, source_category, postcode, country, region, district, city, address,
    phone_mobile, phone_landline, website, email, schedule,
    lat, lon, ratings_count, reviews_count, rating,
    network_id, network_name, attributes,
    vk, ok, facebook, instagram, youtube, twitter,
    telegram, viber, whatsapp,
    yandex_card_url, all_websites, all_phones,
    online_booking, order_url, priority_placement, logo,
    has_phone, has_website, has_email, has_vk, has_instagram, has_telegram
)
SELECT
    TRY_CAST(NULLIF(c0,'') AS BIGINT)                              AS id,
    c1                                                              AS name,
    c2                                                              AS categories,
    ?                                                               AS source_category,
    c3, c4, c5, c6, c7, c8,
    c9, c10, c11, c12, c13,
    TRY_CAST(NULLIF(c14,'') AS DOUBLE)                              AS lat,
    TRY_CAST(NULLIF(c15,'') AS DOUBLE)                              AS lon,
    TRY_CAST(NULLIF(c16,'') AS INTEGER)                             AS ratings_count,
    TRY_CAST(NULLIF(c17,'') AS INTEGER)                             AS reviews_count,
    TRY_CAST(NULLIF(REPLACE(c18, ',', '.'),'') AS DOUBLE)           AS rating,
    c19, c20, c21, c22, c23, c24, c25, c26, c27,
    c28, c29, c30, c31, c32, c33, c34, c35, c36, c37,
    (COALESCE(c9,'') <> '' OR COALESCE(c10,'') <> '')               AS has_phone,
    (COALESCE(c11,'') <> '')                                        AS has_website,
    (COALESCE(c12,'') <> '')                                        AS has_email,
    (COALESCE(c22,'') <> '')                                        AS has_vk,
    (COALESCE(c25,'') <> '')                                        AS has_instagram,
    (COALESCE(c28,'') <> '')                                        AS has_telegram
FROM read_csv(
    ?,
    delim=';',
    header=true,
    names={names},
    all_varchar=true,
    quote='"',
    escape='"',
    ignore_errors=true
);
""".replace("{names}", "[" + ",".join(f"'{c}'" for c in CSV_COLS) + "]")


INDEX_SQL = [
    "CREATE INDEX idx_org_city ON organizations(city);",
    "CREATE INDEX idx_org_region ON organizations(region);",
    "CREATE INDEX idx_org_source_category ON organizations(source_category);",
    "CREATE INDEX idx_org_rating ON organizations(rating);",
    "CREATE INDEX idx_org_has_phone ON organizations(has_phone);",
    "CREATE INDEX idx_org_has_website ON organizations(has_website);",
    "CREATE INDEX idx_org_has_email ON organizations(has_email);",
    "CREATE INDEX idx_org_has_vk ON organizations(has_vk);",
    "CREATE INDEX idx_org_has_instagram ON organizations(has_instagram);",
    "CREATE INDEX idx_org_has_telegram ON organizations(has_telegram);",
]


FACET_SQL = [
    "DROP TABLE IF EXISTS facets_regions;",
    """CREATE TABLE facets_regions AS
       SELECT region, COUNT(*) AS cnt FROM organizations
       WHERE region IS NOT NULL AND region <> '' GROUP BY region ORDER BY region;""",
    "DROP TABLE IF EXISTS facets_cities;",
    """CREATE TABLE facets_cities AS
       SELECT region, city, COUNT(*) AS cnt FROM organizations
       WHERE city IS NOT NULL AND city <> '' GROUP BY region, city ORDER BY city;""",
    "DROP TABLE IF EXISTS facets_categories;",
    """CREATE TABLE facets_categories AS
       SELECT source_category, COUNT(*) AS cnt FROM organizations
       GROUP BY source_category ORDER BY source_category;""",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--csv-dir", default=os.environ.get("CSV_DIR", "/data/csv"))
    p.add_argument("--duckdb", default=os.environ.get("DUCKDB_PATH", "/data/organizations.duckdb"))
    p.add_argument("--limit", type=int, default=0, help="загрузить только первые N файлов (для отладки)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    csv_dir = Path(args.csv_dir)
    if not csv_dir.is_dir():
        print(f"[error] csv-dir не существует: {csv_dir}", file=sys.stderr)
        return 1

    files = sorted(csv_dir.glob("*.csv"))
    if args.limit:
        files = files[: args.limit]
    if not files:
        print(f"[error] нет CSV-файлов в {csv_dir}", file=sys.stderr)
        return 1

    print(f"[info] CSV-файлов: {len(files)}")
    print(f"[info] DuckDB: {args.duckdb}")
    os.makedirs(os.path.dirname(args.duckdb), exist_ok=True)

    try:
        con = duckdb.connect(args.duckdb)
    except duckdb.IOException as exc:
        print(
            "\n[error] Не удалось получить блокировку на файл DuckDB:\n"
            f"  {exc}\n"
            "Возможно backend держит файл на запись. Решения:\n"
            "  1) Перезапустите backend — он теперь подключается read-only:\n"
            "       docker compose restart backend\n"
            "     и снова запустите импорт.\n"
            "  2) Либо остановите backend на время импорта:\n"
            "       docker compose stop backend\n"
            "       docker compose exec backend python /scripts/import.py   # не сработает, контейнер остановлен\n"
            "       # запускайте импорт другим способом\n",
            file=sys.stderr,
        )
        return 1
    con.execute("PRAGMA threads=4;")
    con.execute("DROP TABLE IF EXISTS organizations_new;")
    con.execute(SCHEMA_SQL)

    error_log = Path(args.duckdb).with_suffix(".import_errors.log")
    err_count = 0
    t0 = time.time()
    first_error_printed = False
    with open(error_log, "w", encoding="utf-8") as elog:
        for fp in tqdm(files, desc="импорт CSV", unit="файл"):
            source_category = fp.stem
            try:
                con.execute(INSERT_SQL, [source_category, str(fp)])
            except Exception as exc:  # noqa: BLE001
                err_count += 1
                elog.write(f"{fp}\t{type(exc).__name__}: {exc}\n")
                if not first_error_printed:
                    print(f"\n[error] первая ошибка на файле {fp.name}:\n  {type(exc).__name__}: {exc}\n", file=sys.stderr)
                    first_error_printed = True

    elapsed = time.time() - t0
    print(f"[info] загрузка завершена за {elapsed:.1f} с, ошибок файлов: {err_count}")
    if err_count:
        print(f"[warn] подробности в {error_log}")

    print("[info] атомарный swap таблицы…")
    con.execute("DROP TABLE IF EXISTS organizations;")
    con.execute("ALTER TABLE organizations_new RENAME TO organizations;")

    print("[info] создание индексов…")
    for sql in INDEX_SQL:
        try:
            con.execute(sql)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] индекс не создан: {exc}")

    print("[info] построение facets…")
    for sql in FACET_SQL:
        con.execute(sql)

    total = con.execute("SELECT COUNT(*) FROM organizations").fetchone()[0]
    cities = con.execute("SELECT COUNT(*) FROM facets_cities").fetchone()[0]
    regions = con.execute("SELECT COUNT(*) FROM facets_regions").fetchone()[0]
    cats = con.execute("SELECT COUNT(*) FROM facets_categories").fetchone()[0]

    print("─" * 50)
    print(f"  Всего записей:   {total:>12,}")
    print(f"  Уникальных городов: {cities:>9,}")
    print(f"  Регионов:        {regions:>12,}")
    print(f"  Категорий:       {cats:>12,}")
    print("─" * 50)

    # Сборка дерева категорий.
    try:
        from build_category_tree import build_tree  # type: ignore
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from build_category_tree import build_tree  # type: ignore
    cats = [r[0] for r in con.execute("SELECT source_category FROM facets_categories").fetchall()]
    tree = build_tree(cats)
    tree_path = Path(args.duckdb).parent / "category_tree.json"
    tree_path.write_text(__import__("json").dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[info] дерево категорий: {tree_path}  ({len(tree)} разделов)")

    con.close()
    print("\n[next] Перезапустите backend, чтобы он подхватил свежие данные:")
    print("       docker compose restart backend")
    return 0


if __name__ == "__main__":
    sys.exit(main())
