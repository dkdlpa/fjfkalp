"""Проверка связки: меню, команды, статистика, конвейер отчётов."""
from __future__ import annotations

from app.config import settings


def _labels(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


class TestMenus:
    def test_search_menu_contains_new_modules(self):
        from app.keyboards import get_search_menu

        labels = _labels(get_search_menu("ru"))
        for item in ("GitHub", "VK", "TON-кошелёк", "Лица на фото"):
            assert item in labels

    def test_main_menu_unchanged_shape(self):
        from app.keyboards import get_main_menu

        labels = _labels(get_main_menu("ru"))
        # Отдельных основных кнопок под поисковые модули быть не должно.
        assert "GitHub" not in labels
        assert "VK" not in labels
        assert "Поиск" in labels

    def test_report_link_menu_buttons(self):
        from app.keyboards import get_report_link_menu

        labels = _labels(get_report_link_menu("ru", "https://x/r/a.html", "a" * 32))
        assert "Открыть отчёт" in labels
        assert "Удалить отчёт" in labels

    def test_faces_menu(self):
        from app.keyboards import get_faces_menu

        labels = _labels(get_faces_menu("en"))
        assert "Detect faces" in labels
        assert "Compare two photos" in labels


class TestTelegramTargets:
    def test_gift_slug_accepted(self):
        from app.handlers.telegram import _valid_telegram_target

        assert _valid_telegram_target("PlushPepe-42")
        assert _valid_telegram_target("@durov")
        assert _valid_telegram_target("https://t.me/durov")
        assert not _valid_telegram_target("!!!")


class TestAdminStats:
    async def test_new_modules_are_listed(self, db):
        await db.bump_module("github")
        await db.bump_module("github")
        await db.bump_module("phone_extra")
        stats = await db.get_stats()
        modules = dict(stats["modules"])
        assert modules["github"] == 2
        assert modules["phone_extra"] == 1

    async def test_legacy_request_kinds_survive(self, db):
        await db.record_search(1, "telegram")
        await db.bump_module("github")
        stats = await db.get_stats()
        modules = dict(stats["modules"])
        assert modules.get("telegram") == 1
        assert modules.get("github") == 1


class TestDeliverReport:
    async def test_falls_back_to_file_when_web_disabled(self, db, monkeypatch):
        from app.services import report_host, search_runner

        object.__setattr__(settings, "reports_web_enabled", False)
        object.__setattr__(settings, "reports_public_base", "")
        sent = {}

        class Msg:
            async def answer_document(self, doc, caption=None, reply_markup=None):
                sent["doc"] = doc
                sent["caption"] = caption
                sent["markup"] = reply_markup

        await search_runner.deliver_report(
            Msg(),
            db,
            "ru",
            b"<html></html>",
            filename="x.html",
            caption="cap",
            user_id=42,
        )
        assert sent["doc"] is not None
        assert sent["caption"] == "cap"
        assert report_host.owned(42) is None

    async def test_publishes_link_when_enabled(self, db, monkeypatch, tmp_path):
        from app.services import report_host, search_runner

        object.__setattr__(settings, "reports_web_enabled", True)
        object.__setattr__(settings, "reports_public_base", "https://r.test")
        object.__setattr__(settings, "reports_dir", str(tmp_path))
        object.__setattr__(settings, "reports_ttl_minutes", 20)
        sent = {}

        class Msg:
            async def answer_document(self, doc, caption=None, reply_markup=None):
                sent["caption"] = caption
                sent["markup"] = reply_markup

        await search_runner.deliver_report(
            Msg(),
            db,
            "ru",
            b"<html></html>",
            filename="x.html",
            caption="cap",
            user_id=77,
        )
        owned = report_host.owned(77)
        assert owned is not None
        assert owned.path.exists()
        assert "Открыть отчёт" in _labels(sent["markup"])
        report_host.forget(77)
        report_host.remove(owned.token)
