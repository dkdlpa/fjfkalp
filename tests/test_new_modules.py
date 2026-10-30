"""Тесты новых модулей без сети и без реальных API-ключей."""
from __future__ import annotations

import io
import os
import time

import pytest

from app.config import settings


# ---------------------------------------------------------------- GitHub
class TestGitHub:
    def test_normalize(self):
        from app.services import github

        assert github.normalize_query("@torvalds") == "torvalds"
        assert github.normalize_query("github.com/torvalds/linux") == "torvalds"
        assert github.normalize_query("https://github.com/a/b?x=1") == "a"
        assert github.normalize_query("  torvalds ") == "torvalds"

    def test_username_validation(self):
        from app.services import github

        assert github.is_valid_username("torvalds")
        assert github.is_valid_username("a-b")
        assert not github.is_valid_username("-abc")
        assert not github.is_valid_username("abc-")
        assert not github.is_valid_username("a--b")
        assert not github.is_valid_username("a_b")
        assert not github.is_valid_username("")

    async def test_rate_limit_maps_to_maintenance(self, monkeypatch):
        from app.services import github

        async def fake_get_json(url, params=None, headers=None, **kw):
            return None, "http_403: rate limit"

        monkeypatch.setattr(github, "get_json", fake_get_json)
        result = await github.analyze("torvalds")
        assert result["error"] == "rate_limited"

    async def test_not_found(self, monkeypatch):
        from app.services import github

        async def fake_get_json(url, **kw):
            return None, "http_404"

        monkeypatch.setattr(github, "get_json", fake_get_json)
        result = await github.analyze("nobody-here-xyz")
        assert result["error"] == "not_found"

    async def test_report_escapes(self):
        from app.reports import github_report

        findings = {
            "username": "<script>x</script>",
            "name": "<b>bad</b>",
            "url": "javascript:alert(1)",
            "repos": [],
            "languages": {},
            "emails_found": [],
            "organizations": [],
        }
        html = github_report.build(findings, "x", "ru").decode()
        # Экранирование: опасный текст не превращается в исполняемую разметку.
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html
        assert 'href="javascript:alert(1)"' not in html


# ---------------------------------------------------------------- VK
class TestVK:
    def test_normalize_and_validate(self):
        from app.services import vk

        assert vk.normalize_vk_id("https://vk.com/id123") == "123"
        assert vk.normalize_vk_id("vk.com/durov") == "durov"
        assert vk.normalize_vk_id("@durov") == "durov"
        assert vk.normalize_vk_id("12345") == "12345"
        assert vk.is_valid_vk_id("durov")
        assert not vk.is_valid_vk_id("")

    async def test_not_configured(self, monkeypatch):
        from app.services import vk

        object.__setattr__(settings, "vk_token", "")
        result = await vk.analyze("durov")
        assert result["error"] == "not_configured"

    async def test_api_error_mapping(self, monkeypatch):
        from app.services import vk

        object.__setattr__(settings, "vk_token", "token")

        async def fake_get_json(url, **kw):
            return {"error": {"error_code": 5, "error_msg": "auth"}}, None

        monkeypatch.setattr(vk, "get_json", fake_get_json)
        result = await vk.analyze("durov")
        assert result["error"] == "auth_error"

    async def test_report(self):
        from app.reports import vk_report

        findings = {
            "profile": {"id": 1, "first_name": "A", "last_name": "B", "url": "https://vk.com/a"},
            "groups": {"count": 1, "items": [{"name": "G", "screen_name": "g", "members": 5}]},
            "friends": {"count": 0, "items": []},
            "wall": {"count": 0, "items": []},
            "photos": {"count": 0, "items": []},
        }
        html = vk_report.build(findings, "a", "ru").decode()
        assert "VK" in html
        assert "A B" in html


# ---------------------------------------------------------------- see.tg
class TestSeeTg:
    async def test_no_token(self, monkeypatch):
        from app.services import see_tg

        object.__setattr__(settings, "see_tg_token", "")

        with pytest.raises(see_tg.SeeTgError) as exc:
            await see_tg.owner("1")
        assert exc.value.kind == "not_configured"

    async def test_quota_is_temporary(self, monkeypatch):
        from app.services import see_tg

        object.__setattr__(settings, "see_tg_token", "1:abc")

        async def fake_get_json(url, **kw):
            return None, "http_402: payment required"

        monkeypatch.setattr(see_tg, "get_json", fake_get_json)
        with pytest.raises(see_tg.SeeTgError) as exc:
            await see_tg.owner("1")
        assert exc.value.kind == "quota"
        assert see_tg.user_key(exc.value) == "source_maintenance"

    async def test_expensive_disabled(self, monkeypatch):
        from app.services import see_tg

        object.__setattr__(settings, "see_tg_token", "1:abc")
        object.__setattr__(settings, "see_tg_expensive", False)

        with pytest.raises(see_tg.SeeTgError) as exc:
            await see_tg.owners_search("durov")
        assert exc.value.kind == "expensive_disabled"
        assert see_tg.user_key(exc.value) == "see_tg_expensive_disabled"

    def test_helpers(self):
        from app.services import see_tg

        assert see_tg.nano_to_ton(1_000_000_000) == 1.0
        assert see_tg.fmt_ton(1_500_000_000) == "1.50 TON"
        assert see_tg.gift_title({"title": "Pepe", "num": 42}) == "Pepe #42"
        payload = {"items": [{"id": 1}], "nextCursor": "abc", "total": 5}
        assert see_tg.items_of(payload) == [{"id": 1}]
        assert see_tg.next_cursor(payload) == "abc"
        assert see_tg.total_of(payload) == 5

    async def test_telegram_profile_analysis(self, monkeypatch):
        from app.services import see_tg
        from app.services import telegram as tg

        object.__setattr__(settings, "see_tg_token", "1:abc")

        async def fake_resolve(ref):
            return {"type": "owner", "owner": {"see_id": "42", "ref": "42"}}

        async def fake_owner(ref):
            return {"see_id": "42", "first_name": "Ivan", "username": "ivan",
                    "gifts_count": 2, "usernames": ["ivan", "iv"]}

        async def fake_gifts(ref, **kw):
            return {"items": [{"title": "Pepe", "num": 1}], "total": 2, "nextCursor": None}

        async def fake_history(ref, **kw):
            return {"items": [{"type": "transfer", "at": "2026-01-01T00:00:00Z"}]}

        monkeypatch.setattr(see_tg, "resolve", fake_resolve)
        monkeypatch.setattr(see_tg, "owner", fake_owner)
        monkeypatch.setattr(see_tg, "owner_gifts", fake_gifts)
        monkeypatch.setattr(see_tg, "owner_history", fake_history)

        result = await tg.analyze("@ivan")
        assert result["error"] is None
        assert result["profile"]["username"] == "ivan"
        assert result["gifts"]["items"][0]["title"] == "Pepe"
        assert result["history"]["items"]

    def test_report_has_attribution(self):
        from app.reports import see_tg_report

        findings = {
            "profile": {"first_name": "Ivan", "username": "ivan", "gifts_count": 1},
            "gifts": {"items": [{"title": "Pepe", "num": 1}], "total": 1},
            "history": {"items": []},
            "notes": [],
        }
        html = see_tg_report.build_profile(findings, "@ivan", "ru").decode()
        assert "see.tg" in html
        assert "https://see.tg" in html


# ---------------------------------------------------------------- Phone extra
class TestPhoneExtra:
    def test_norm_phone(self):
        from app.services import phone_extra

        assert phone_extra.norm_phone("+7 (999) 123-45-67") == "9991234567"
        assert phone_extra.norm_phone("89991234567") == "9991234567"
        assert phone_extra.norm_phone("+1 555 000 0000") == "15550000000"

    def test_build_url(self):
        from app.services import phone_extra

        assert phone_extra.build_url("https://x/{phone}.csv", "999") == "https://x/999.csv"
        assert (
            phone_extra.build_url("https://x?p={phone_raw}", "999") == "https://x?p=+999"
        )

    def test_csv_search_exact_match(self):
        from app.services import phone_extra

        text = "phone,name,extra\n+79991234567,Ivan,x\n79991234567,Ivan,y\n+79990000000,Petr,z\n"
        headers, rows = phone_extra.parse_csv_records(text)
        found = phone_extra.search_in_rows(headers, rows, "+79991234567")
        assert list(found["name"]) == ["Ivan"]
        assert "Petr" not in str(found)

    async def test_disabled_by_default(self, monkeypatch):
        from app.services import phone_extra

        object.__setattr__(settings, "phone_extra_enabled", False)
        result = await phone_extra.analyze("+79991234567")
        assert result["status"] == "disabled"

    async def test_fetch_and_extract(self, monkeypatch):
        from app.services import phone_extra

        object.__setattr__(settings, "phone_extra_enabled", True)
        object.__setattr__(settings, "phone_extra_url_template", "https://x/{phone}.csv")
        object.__setattr__(settings, "phone_extra_source_label", "test-src")

        async def fake_fetch_text(url, **kw):
            return 200, "phone,name\n+79991234567,Ivan\n"

        monkeypatch.setattr(phone_extra, "fetch_text", fake_fetch_text)
        result = await phone_extra.analyze("+7 999 123-45-67")
        assert result["status"] == "ok"
        # Два значения: сам номер и имя из строки совпадения.
        assert result["values_count"] == 2
        assert result["matches"]["name"] == ["Ivan"]
        assert result["source"] == "test-src"

    async def test_error_is_isolated(self, monkeypatch):
        from app.services import phone_extra

        object.__setattr__(settings, "phone_extra_enabled", True)
        object.__setattr__(settings, "phone_extra_url_template", "https://x/{phone}.csv")

        async def fake_fetch_text(url, **kw):
            return 0, ""

        monkeypatch.setattr(phone_extra, "fetch_text", fake_fetch_text)
        result = await phone_extra.analyze("+79991234567")
        assert result["status"] == "error"

    async def test_phone_analyze_survives_extra_error(self, monkeypatch):
        from app.services import phone, phone_extra

        async def boom(target):
            raise RuntimeError("boom")

        monkeypatch.setattr(phone_extra, "analyze", boom)
        result = await phone.analyze("+79991234567")
        assert result["valid"] is True
        assert result["extra"]["status"] == "error"


# ---------------------------------------------------------------- Faces
class TestFaces:
    def _png(self, size=(64, 64), color=(200, 100, 50)):
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", size, color).save(buf, format="PNG")
        return buf.getvalue()

    def test_describe(self):
        from app.services import faces

        facts = faces.describe(self._png((100, 60)), with_faces=False)
        assert facts.width == 100 and facts.height == 60
        assert facts.orientation == "landscape"
        assert facts.error is None

    def test_compare_same_vs_different(self):
        from app.services import faces

        a = self._png((80, 80), (10, 10, 10))
        b = self._png((80, 80), (10, 10, 10))
        c = self._png((80, 80), (250, 250, 250))
        same = faces.compare(a, b)
        diff = faces.compare(a, c)
        assert same["similarity"] >= diff["similarity"]
        assert same["verdict"] in {"same", "similar"}

    def test_not_an_image(self):
        from app.services import faces

        result = faces.compare(b"not an image", b"also not")
        assert result["error"] == "not_an_image"

    def test_cv_availability_is_bool(self):
        from app.services import faces

        assert isinstance(faces.cv_available(), bool)


# ---------------------------------------------------------------- Report host
class TestReportHost:
    def test_publish_disabled(self, monkeypatch, tmp_path):
        from app.services import report_host

        object.__setattr__(settings, "reports_web_enabled", False)
        object.__setattr__(settings, "reports_public_base", "")
        assert report_host.publish(b"<html></html>") is None

    def test_publish_and_prune(self, monkeypatch, tmp_path):
        from app.services import report_host

        object.__setattr__(settings, "reports_web_enabled", True)
        object.__setattr__(settings, "reports_public_base", "https://r.example")
        object.__setattr__(settings, "reports_dir", str(tmp_path))
        object.__setattr__(settings, "reports_ttl_minutes", 10)
        object.__setattr__(settings, "reports_max_files", 10)

        published = report_host.publish(b"<html>x</html>")
        assert published is not None
        assert published.url.startswith("https://r.example/r/")
        assert published.path.exists()
        assert len(published.token) == 32

        # Имя непредсказуемое и не последовательное.
        another = report_host.publish(b"<html>y</html>")
        assert another.token != published.token

        # Просроченный файл удаляется.
        old = published.path
        past = time.time() - 3600
        os.utime(old, (past, past))
        removed = report_host.prune()
        assert removed >= 1
        assert not old.exists()

    def test_prune_does_not_touch_foreign_files(self, monkeypatch, tmp_path):
        from app.services import report_host

        object.__setattr__(settings, "reports_dir", str(tmp_path))
        keep = tmp_path / "keep.txt"
        keep.write_text("keep")
        stray = tmp_path / "notes.html"
        stray.write_text("stray")
        past = time.time() - 99999
        os.utime(keep, (past, past))
        os.utime(stray, (past, past))
        report_host.prune()
        assert keep.exists()
        assert stray.exists()

    def test_remove_requires_valid_token(self, monkeypatch, tmp_path):
        from app.services import report_host

        object.__setattr__(settings, "reports_dir", str(tmp_path))
        assert report_host.remove("../../../etc/passwd") is False
        assert report_host.remove("short") is False

    def test_path_traversal_rejected(self, monkeypatch, tmp_path):
        from app.services import report_host

        object.__setattr__(settings, "reports_dir", str(tmp_path))
        assert report_host._resolve_request_path("/r/../../etc/passwd") is None
        assert report_host._resolve_request_path("/r/" + "a" * 32 + ".html") is not None

    def test_report_menu_delete_ownership(self):
        from app.services import report_host

        report_host.forget(1)
        assert report_host.owned(1) is None


# ---------------------------------------------------------------- New reports
class TestNewReports:
    def test_phone_report_two_blocks(self):
        from app.reports import phone_report

        findings = {
            "valid": True,
            "e164": "+79991234567",
            "country_code": 7,
            "region": "RU",
            "carrier": "MTS",
            "number_type": "mobile",
            "timezones": ["Europe/Moscow"],
            "links": {"whatsapp": "https://wa.me/79991234567"},
            "extra": {
                "status": "ok",
                "source": "src",
                "matches": {"name": ["Ivan"]},
                "values_count": 1,
                "url_host": "x.test",
            },
        }
        html = phone_report.build(findings, "+79991234567", "ru").decode()
        assert "Телефон" in html
        assert "Дополнительные результаты" in html
        assert "Ivan" in html

    def test_phone_report_extra_error_still_builds(self):
        from app.reports import phone_report

        findings = {
            "valid": True,
            "e164": "+79991234567",
            "links": {},
            "extra": {"status": "error"},
        }
        html = phone_report.build(findings, "+79991234567", "ru").decode()
        assert "Телефон" in html
        assert "техническими работами" in html

    def test_base_report_navigation_and_copy(self):
        from app.reports.base import render_report

        sections = [{"id": f"s{i}", "title": f"T{i}", "rows": [("k", "v", True)]} for i in range(5)]
        html = render_report(title="X", sections=sections).decode()
        assert 'nav class="nav"' in html
        assert "data-copy" in html
        assert 'href="#s0"' in html
        assert "noindex" in html

    def test_small_report_has_no_nav(self):
        from app.reports.base import render_report

        html = render_report(title="X", sections=[{"id": "a", "title": "A", "rows": [("k", "v")]}]).decode()
        assert 'class="nav"' not in html