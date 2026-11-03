from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.deep_analysis.telegram import normalize_telegram_posts
from app.services.deep_analysis.vk import normalize_vk_posts
from app.services.deep_analysis.website import prioritize_site_urls


def test_telegram_normalizes_thirty_latest_posts_and_media_descriptors():
    class MessageMediaPhoto:
        pass

    messages = [
        SimpleNamespace(
            id=index,
            date=datetime(2026, 8, 9, tzinfo=timezone.utc),
            text=f"Пост {index}",
            views=index * 10,
            forwards=index,
            media=MessageMediaPhoto(),
        )
        for index in range(31)
    ]

    posts = normalize_telegram_posts(messages, max_posts=30)

    assert len(posts) == 30
    assert posts[0] == {
        "id": 0,
        "date": "2026-08-09T00:00:00+00:00",
        "text": "Пост 0",
        "views": 0,
        "forwards": 0,
        "media": [{"type": "photo", "title": "Фотография"}],
    }
    assert "raw" not in posts[0]


def test_vk_normalizes_public_photo_and_video_attachments_without_raw_payload():
    posts = normalize_vk_posts([{
        "id": 4,
        "date": 1_723_161_600,
        "text": "Новая акция",
        "likes": {"count": 12},
        "comments": {"count": 3},
        "reposts": {"count": 2},
        "attachments": [
            {"type": "photo", "photo": {"sizes": [{"width": 300, "url": "https://img.test/s.jpg"}, {"width": 1200, "url": "https://img.test/l.jpg"}]}},
            {"type": "video", "video": {"title": "Обзор", "owner_id": -4, "id": 9}},
        ],
    }])

    assert posts == [{
        "id": 4,
        "date": "2024-08-09T00:00:00+00:00",
        "text": "Новая акция",
        "likes": 12,
        "comments": 3,
        "reposts": 2,
        "media": [
            {"type": "photo", "title": "Фотография", "url": "https://img.test/l.jpg"},
            {"type": "video", "title": "Обзор", "url": "https://vk.com/video-4_9"},
        ],
    }]


def test_website_prioritizes_internal_pages_and_limits_crawl_to_five():
    urls = prioritize_site_urls(
        "https://clinic.test/",
        [
            ("https://clinic.test/blog", "Блог"),
            ("https://clinic.test/about", "О компании"),
            ("https://clinic.test/prices", "Цены"),
            ("https://external.test/ad", "Реклама"),
            ("https://clinic.test/contact", "Контакты"),
            ("https://clinic.test/services", "Услуги"),
        ],
    )

    assert urls == [
        "https://clinic.test/",
        "https://clinic.test/services",
        "https://clinic.test/prices",
        "https://clinic.test/contact",
        "https://clinic.test/about",
    ]
