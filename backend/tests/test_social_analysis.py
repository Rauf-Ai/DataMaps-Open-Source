from app.services.social_analysis_service import extract_social_urls


def test_extract_supported_social_urls_and_deduplicate():
    rows = extract_social_urls(
        "VK https://vk.com/datamaps; Telegram https://t.me/datamaps, "
        "https://vk.com/datamaps; сайт https://datamaps.ru"
    )
    assert rows == [
        ("vk", "https://vk.com/datamaps"),
        ("telegram", "https://t.me/datamaps"),
    ]


def test_extract_ignores_unsupported_and_plain_text():
    assert extract_social_urls("instagram без ссылки; https://example.com/account") == []
