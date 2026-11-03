from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(default="sqlite:////data/app.db", alias="DATABASE_URL")
    duckdb_path: str = Field(default="/data/organizations.duckdb", alias="DUCKDB_PATH")

    secret_key: str = Field(default="dev-secret-change-me", alias="SECRET_KEY")
    access_token_expire_minutes: int = Field(default=15, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(default=30, alias="REFRESH_TOKEN_EXPIRE_DAYS")
    cookie_secure: bool = Field(default=False, alias="COOKIE_SECURE")
    cookie_domain: str = Field(default="", alias="COOKIE_DOMAIN")

    yookassa_shop_id: str = Field(default="", alias="YOOKASSA_SHOP_ID")
    yookassa_secret_key: str = Field(default="", alias="YOOKASSA_SECRET_KEY")
    yookassa_return_url: str = Field(default="http://localhost/dashboard.html", alias="YOOKASSA_RETURN_URL")
    yookassa_recurring_enabled: bool = Field(default=False, alias="YOOKASSA_RECURRING_ENABLED")

    smtp_host: str = Field(default="mailhog", alias="SMTP_HOST")
    smtp_port: int = Field(default=1025, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_tls: bool = Field(default=False, alias="SMTP_TLS")
    email_from: str = Field(default="noreply@spravochnik.local", alias="EMAIL_FROM")
    # Требовать подтверждение email при регистрации. Позволяет включить SMTP
    # (для тестов/сброса пароля), не включая обязательное подтверждение, пока
    # не убедимся, что письма реально доходят (DNS/SPF/DKIM настроены).
    require_email_verification: bool = Field(default=True, alias="REQUIRE_EMAIL_VERIFICATION")

    app_url: str = Field(default="http://localhost", alias="APP_URL")
    local_mode: bool = Field(default=False, alias="LOCAL_MODE")
    admin_email: str = Field(default="admin@spravochnik.local", alias="ADMIN_EMAIL")
    free_credits_on_register: int = Field(default=20, alias="FREE_CREDITS_ON_REGISTER")
    export_file_ttl_hours: int = Field(default=24, alias="EXPORT_FILE_TTL_HOURS")
    export_max_rows: int = Field(default=200000, alias="EXPORT_MAX_ROWS")

    csv_dir: str = Field(default="/data/csv", alias="CSV_DIR")
    export_dir: str = Field(default="/data/exports", alias="EXPORT_DIR")

    # LLM (OpenAI-compatible endpoint для AI-сводок отзывов)
    llm_base_url: str = Field(default="https://openrouter.ai/api/v1", alias="LLM_BASE_URL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_model: str = Field(default="deepseek/deepseek-chat:free", alias="LLM_MODEL")
    llm_timeout: int = Field(default=60, alias="LLM_TIMEOUT")
    stt_model: str = Field(default="openai/whisper-large-v3", alias="STT_MODEL")

    # Тарификация анализа отзывов
    reviews_price_base: int = Field(default=5, alias="REVIEWS_PRICE_BASE")
    reviews_price_base_deep: int = Field(default=15, alias="REVIEWS_PRICE_BASE_DEEP")
    reviews_price_per_review: float = Field(default=0.1, alias="REVIEWS_PRICE_PER_REVIEW")

    # Живой парсинг карт. 2ГИС оставлен только для чтения старых задач и не предлагается пользователям.
    yandex_search_api_url: str = Field(
        default="https://search-maps.yandex.ru/v1/", alias="YANDEX_SEARCH_API_URL")
    yandex_search_api_key: str = Field(default="", alias="YANDEX_SEARCH_API_KEY")
    twogis_api_key: str = Field(default="", alias="TWOGIS_API_KEY")
    # Прокси для Google-парсера (пусто = без прокси). Формат: http://user:pass@host:port
    google_proxy: str = Field(default="", alias="GOOGLE_PROXY")

    # Тарификация живого парсинга
    parser_price_per_org: float = Field(default=0.5, alias="PARSER_PRICE_PER_ORG")
    parser_price_org_zip: int = Field(default=20, alias="PARSER_PRICE_ORG_ZIP")
    parser_price_enrich: int = Field(default=3, alias="PARSER_PRICE_ENRICH")

    # ИИ-агент техподдержки + Telegram-бот для аппрува действий
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_admin_chat_id: str = Field(default="", alias="TELEGRAM_ADMIN_CHAT_ID")
    telegram_webhook_secret: str = Field(default="", alias="TELEGRAM_WEBHOOK_SECRET")
    support_llm_model: str = Field(default="deepseek/deepseek-v4-flash", alias="SUPPORT_LLM_MODEL")
    support_guest_daily_limit: int = Field(default=20, alias="SUPPORT_GUEST_DAILY_LIMIT")
    support_upload_dir: str = Field(default="/data/support_uploads", alias="SUPPORT_UPLOAD_DIR")
    support_max_upload_mb: int = Field(default=8, alias="SUPPORT_MAX_UPLOAD_MB")


@lru_cache
def get_settings() -> Settings:
    return Settings()
