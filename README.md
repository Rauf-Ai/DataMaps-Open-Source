# DataMaps Open Source

[Инструкция на русском](README.ru.md)

> **Version notice (2026-11-03):** This repository is based on an older local DataMaps codebase. The newer version is on a suspended RUVDS server and has not yet been recovered. This release is usable locally, but it does **not** represent the latest DataMaps version. We will update it only after recovering and reviewing the server files.

DataMaps is a self-hosted catalog for searching organization data and exporting results to XLSX. The local edition is free: no credits, subscription, payment account, or API key is needed for CSV search and export. Source code is available under the [MIT license](LICENSE).

The repository includes **only synthetic demo organizations**. Bring your own CSV files for real data. The original proprietary dataset is not included.

## Quick start

Requirements: Docker Engine with Compose v2. The demo needs only a small amount of disk space; larger imports need space for both source CSV and DuckDB.

```bash
git clone https://github.com/Rauf-Ai/DataMaps-Open-Source.git
cd DataMaps-Open-Source
cp .env.example .env
# Set SECRET_KEY in .env to a random value, for example from: openssl rand -hex 32
docker compose up -d --build
docker compose exec backend python /scripts/import.py
docker compose restart backend
```

Open [http://localhost:8090](http://localhost:8090). Search the synthetic demo data immediately. To export, register a local account and log in. Email confirmation is disabled in the local setup.

The backend API documentation is at [http://localhost:8090/api/docs](http://localhost:8090/api/docs). The Compose setup publishes only the frontend on `127.0.0.1:8090`. Change `DATAMAPS_PORT` and `APP_URL` together if that port is occupied.

## Use your own data

The importer accepts UTF-8, semicolon-delimited CSV files with a header and the 38 columns described in [`scripts/import.py`](scripts/import.py). It uses each filename as the source category. Set `CSV_SOURCE_DIR` in `.env` to an absolute path to your CSV directory, then run:

```bash
docker compose up -d
docker compose exec backend python /scripts/import.py
docker compose restart backend
```

Import replaces the previous DuckDB catalog. Local SQLite accounts, export files, and the imported catalog live under `data/`, which Git ignores. Back up that directory if you need to keep it. The generated sample CSV is in `examples/demo_csv/`.

## Optional integrations

Manual CSV search and XLSX export work without external services. AI filter suggestions and summaries need your own OpenAI-compatible LLM endpoint (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`). Live map searches need a provider key such as `YANDEX_SEARCH_API_KEY`. Provider terms and charges, if any, are separate from DataMaps. These functions can be left unconfigured.

`LOCAL_MODE=true` disables all checkout and payment webhook endpoints, stops subscription renewal jobs, and removes credit checks from catalog export and analysis quotas. The local interface shows no purchase flow. `EXPORT_MAX_ROWS` remains a per-file safety limit and background jobs have a three-task concurrency limit.

## Development

Backend: Python 3.12, FastAPI, DuckDB, SQLite. Frontend: React 18, Vite, Gravity UI.

```bash
cd frontend && npm install && npm test && npm run build
cd ../backend && python -m pytest tests
```

The default Docker setup serves on your own machine. If you expose it on a network, set a strong `SECRET_KEY`, configure HTTPS and `COOKIE_SECURE`, and review the authentication and external-provider settings first.
