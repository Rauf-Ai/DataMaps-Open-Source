import pytest
from pathlib import Path
from app.services.deep_analysis.security import validate_url, SSRFValidationError
from app.services.deep_analysis.dedup import Deduplicator

def test_ssrf_blocks_private_ip():
    with pytest.raises(SSRFValidationError, match="публичным IP"):
        validate_url("http://192.168.1.1/api")

def test_ssrf_blocks_localhost():
    with pytest.raises(SSRFValidationError, match="localhost"):
        validate_url("http://localhost:8080/admin")

def test_ssrf_blocks_credentials():
    with pytest.raises(SSRFValidationError, match="учетные данные"):
        validate_url("https://user:pass@example.com/data")

def test_ssrf_blocks_blocked_port():
    with pytest.raises(SSRFValidationError, match="Порт 22 заблокирован"):
        validate_url("http://example.com:22/")

def test_ssrf_allows_valid_public_url():
    # Должно пройти без исключений
    result = validate_url("https://example.com/public/page")
    assert result == "https://example.com/public/page"

def test_deduplication_by_hash_and_url():
    dedup = Deduplicator(Path("/tmp/test_dedup"))
    data = b"hello world"
    url1 = "https://example.com/file.txt"
    url2 = "https://example.com/file.txt?version=2"
    url3 = "https://example.com/other.txt"

    # Первый раз должен разрешить (вернет хеш, а не "deduplicated")
    res1 = dedup.should_download(url1, data)
    assert res1 != "deduplicated"

    # Тот же URL с параметрами (canonical URL совпадает) -> дедупликация
    assert dedup.should_download(url2, data) == "deduplicated"

    # Другой URL, но те же данные (хеш совпадает) -> дедупликация
    assert dedup.should_download(url3, data) == "deduplicated"

    # Новые данные -> разрешить
    res4 = dedup.should_download(url3, b"different data")
    assert res4 != "deduplicated"
