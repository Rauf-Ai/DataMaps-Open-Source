"""Create a tiny, entirely synthetic CSV to try the local catalog."""
from __future__ import annotations

import csv
from pathlib import Path

ROWS = [
    (1, "Пример: Мастерская Север", "Москва", "Москва", "ул. Демонстрационная, 1", "+7 000 000-00-01", "https://example.org", "hello@example.org", 4.7),
    (2, "Пример: Студия Восток", "Московская область", "Химки", "ул. Учебная, 2", "+7 000 000-00-02", "https://example.com", "contact@example.com", 4.4),
    (3, "Пример: Ателье Юг", "Краснодарский край", "Краснодар", "ул. Тестовая, 3", "+7 000 000-00-03", "", "", 4.9),
]


def main() -> None:
    output = Path(__file__).resolve().parent.parent / "examples" / "demo_csv" / "Демонстрационные организации.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=";", lineterminator="\n")
        writer.writerow([f"column_{i}" for i in range(38)])
        for org_id, name, region, city, address, phone, site, email, rating in ROWS:
            cols = [""] * 38
            cols[0], cols[1], cols[2] = org_id, name, "Услуги"
            cols[4], cols[5], cols[7] = "Россия", region, city
            cols[8], cols[9], cols[11], cols[12] = address, phone, site, email
            cols[17], cols[18] = 12, rating
            writer.writerow(cols)
    print(output)


if __name__ == "__main__":
    main()
