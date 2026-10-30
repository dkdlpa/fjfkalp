"""Задача 1: списание идёт по user_id, а не по from_user сообщения бота."""
from types import SimpleNamespace

from app.services.search_runner import SearchSpec
from app.services import search_runner


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


class FakeMessage:
    """Сообщение бота: from_user — это сам бот (id=999)."""

    def __init__(self, bot):
        self.from_user = SimpleNamespace(id=999)  # ID бота
        self.bot = bot
        self.answers = []
        self.docs = []

    async def answer(self, text, **kw):
        self.answers.append(text)
        return self

    async def answer_document(self, doc, **kw):
        self.docs.append(kw.get("caption", ""))
        return self

    async def delete(self):
        pass


def _spec():
    async def analyze(v):
        return {"ok": True}

    return SearchSpec(
        kind="phone",
        normalize=lambda v: v,
        validate=lambda v: True,
        analyze=analyze,
        build_report=lambda f, v, lang: b"<html></html>",
        filename_prefix="t",
    )


async def test_charge_targets_user_not_bot(db):
    user_id = 42
    await db.upsert_user(user_id, None, None)
    before = await db.trial_state(user_id)

    msg = FakeMessage(FakeBot())
    await search_runner.run_search(msg, db, "ru", _spec(), "+79990000000", user_id=user_id)

    after = await db.trial_state(user_id)
    assert after["used"] == before["used"] + 1
    # бот не должен появиться в users и не должен получить кредитов
    assert await db.get_user_profile(999) is None
    assert await db.get_credits(999) == 0


async def test_referral_bonus_not_paid_to_bot(db):
    # бот-реферер не должен получать бонус за «своего» реферала
    await db.upsert_user(7, None, None)
    await db.set_referrer(7, 999)  # 999 — не существует в users
    msg = FakeMessage(FakeBot())
    await search_runner.run_search(msg, db, "ru", _spec(), "+79990000001", user_id=7)
    assert await db.get_credits(999) == 0


async def test_second_search_rejected_while_active(db):
    user_id = 5
    await db.upsert_user(user_id, None, None)
    search_runner._active_users.add(user_id)
    try:
        msg = FakeMessage(FakeBot())
        await search_runner.run_search(msg, db, "ru", _spec(), "x", user_id=user_id)
        assert any("выполняется" in a.lower() for a in msg.answers)
    finally:
        search_runner._active_users.discard(user_id)