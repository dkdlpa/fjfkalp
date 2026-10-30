"""Тесты валидаторов, распознавания и парсеров (C1, C3, C4, C6)."""
from types import SimpleNamespace

from app.services.ip_domain import detect_kind
from app.services.username_search import parse_username
from app.services.email_search import is_valid_email
from app.validators import parse_telegram


def _msg(text):
    return SimpleNamespace(text=text)


def _referrer(text):
    from app.handlers.user import _parse_referrer

    return _parse_referrer(_msg(text))


class TestReferrer:
    def test_valid(self):
        assert _referrer("/start ref_123") == 123

    def test_rejects_junk(self):
        assert _referrer("/start ref_abc") is None
        assert _referrer("/start ref_-5") is None
        assert _referrer("/start ref_") is None
        assert _referrer("/start") is None
        assert _referrer("/start ref_12a3") is None

    def test_rejects_unicode_digits(self):
        assert _referrer("/start ref_١٢٣") is None

    def test_rejects_too_long_and_overflow(self):
        assert _referrer("/start ref_1234567890123") is None
        assert _referrer("/start ref_99999999999999999999") is None


class TestDetectKind:
    def test_ipv4(self):
        assert detect_kind("8.8.8.8") == ("ip", "8.8.8.8")

    def test_ipv6(self):
        assert detect_kind("2606:4700:4700::1111") == ("ip", "2606:4700:4700::1111")

    def test_ipv6_url(self):
        assert detect_kind("http://[2606:4700:4700::1111]/x") == ("ip", "2606:4700:4700::1111")

    def test_private(self):
        assert detect_kind("192.168.1.1")[0] == "ip_private"
        assert detect_kind("127.0.0.1")[0] == "ip_private"
        assert detect_kind("[::1]")[0] == "ip_private"

    def test_domain(self):
        assert detect_kind("example.com") == ("domain", "example.com")

    def test_domain_with_path(self):
        assert detect_kind("https://example.com/a?b=1") == ("domain", "example.com")

    def test_idn(self):
        kind = detect_kind("пример.рф")
        assert kind is not None and kind[0] == "domain" and kind[1].startswith("xn--")


class TestParseUsername:
    def test_plain(self):
        assert parse_username("@name") == "name"
        assert parse_username("name_1") == "name_1"

    def test_tiktok_link(self):
        assert parse_username("https://tiktok.com/@name") == "name"
        assert parse_username("https://www.tiktok.com/@name/") == "name"

    def test_invalid(self):
        assert parse_username("has space") is None


class TestEmail:
    def test_valid(self):
        assert is_valid_email("user@example.com")

    def test_too_long(self):
        assert not is_valid_email("a" * 65 + "@example.com")
        assert not is_valid_email("a@" + "b" * 250 + ".com")

    def test_double_dot(self):
        assert not is_valid_email("user..name@example.com")
        assert not is_valid_email("user@example..com")

    def test_domain_edge_hyphen(self):
        assert not is_valid_email("user@-example.com")
        assert not is_valid_email("user@example-.com")


class TestParseTelegram:
    def test_username_and_id(self):
        assert parse_telegram("@username") == ("username", "username")
        assert parse_telegram("https://t.me/someone") == ("username", "someone")
        assert parse_telegram("123456") == ("id", "123456")