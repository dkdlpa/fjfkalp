"""Регресс: битый banner_file_id не ломает /start — идёт перезагрузка баннера."""
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest

from app.handlers import user as user_mod


class FakeBot:
    def __init__(self):
        self.send_photo_calls = []
        self._fail_file_id = True

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None):
        self.send_photo_calls.append(photo)
        if isinstance(photo, str) and self._fail_file_id:
            raise TelegramBadRequest(method=None, message="wrong file identifier/HTTP URL specified")
        return SimpleNamespace(
            chat=SimpleNamespace(id=chat_id), message_id=1,
            photo=[SimpleNamespace(file_id="new_fid")],
        )


async def test_invalid_file_id_reloads(db, tmp_path, monkeypatch):
    from app.config import settings

    png = tmp_path / "www.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    object.__setattr__(settings, "banner_path", str(png))
    await db.set_setting("banner_file_id", "STALE_ID")

    bot = FakeBot()
    message = SimpleNamespace(
        chat=SimpleNamespace(id=123), bot=bot, message_id=5,
        async_answer=None,
    )
    monkeypatch.setattr(user_mod.banner, "send_first", _fake_first)

    async def fake_answer(text, **kw):
        return SimpleNamespace()

    message.answer = fake_answer

    await user_mod.show_banner(None, db, "MAIN", None, message=message)
    # был вызван send_photo со строкой STALE_ID, затем перезагрузка
    assert "STALE_ID" in bot.send_photo_calls
    assert await db.get_setting("banner_file_id") == "new_fid"


async def _fake_first(bot, db, chat_id, text, reply_markup=None):
    await bot.send_photo(chat_id, object(), caption=text, reply_markup=reply_markup)
    await db.set_setting("banner_file_id", "new_fid")
    return SimpleNamespace()
