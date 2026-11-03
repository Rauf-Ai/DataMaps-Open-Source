from app.services.maps.socials import (
    classify_social_url,
    has_social_network,
    normalize_social_url,
)


def test_classifies_current_vk_and_max_profile_urls():
    assert classify_social_url("https://vk.ru/cafe_moscow") == "vk"
    assert classify_social_url("https://max.ru/u/AbC123") == "max"


def test_rejects_bare_or_yandex_owned_social_urls():
    assert normalize_social_url("https://vk.ru") is None
    assert normalize_social_url("https://vk.com/yandex.maps") is None


def test_filter_matches_vk_ru_and_max_ru_without_substring_false_positive():
    assert has_social_network("https://vk.ru/cafe", "vk")
    assert has_social_network("https://max.ru/u/cafe", "max")
    assert not has_social_network("https://example.com/vk.ru/cafe", "vk")
