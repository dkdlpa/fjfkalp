"""Задача 2: TermsMiddleware блокирует доступ без согласия."""
from types import SimpleNamespace

from app.middlewares import TermsMiddleware


class FakeMsg:
    def __init__(self, user_id, text=""):
        self.from_user = SimpleNamespace(id=user_id, is_bot=False)
        self.text = text
        self.answers = []

    async def answer(self, text, **kw):
        self.answers.append(text)
        return self


class FakeCb:
    def __init__(self, user_id, data):
        self.from_user = SimpleNamespace(id=user_id, is_bot=False)
        self.data = data
        self.answered = 0
        self.message = None

    async def answer(self, *a, **kw):
        self.answered += 1


async def _run(mw, event, db):
    handled = []

    async def handler(ev, data):
        handled.append(True)
        return "ok"

    await mw(handler, event, {"db": db, "state": None})
    return handled


async def test_unaccepted_message_blocked(db):
    await db.upsert_user(1, None, None)
    mw = TermsMiddleware()
    handled = await _run(mw, FakeMsg(1, "@username"), db)
    assert handled == []


async def test_unaccepted_callback_blocked_one_answer(db):
    await db.upsert_user(2, None, None)
    mw = TermsMiddleware()
    cb = FakeCb(2, "m:search")
    handled = await _run(mw, cb, db)
    assert handled == []
    assert cb.answered == 1


async def test_start_always_passes(db):
    mw = TermsMiddleware()
    handled = await _run(mw, FakeMsg(3, "/start ref_1"), db)
    assert handled == [True]


async def test_accept_callback_passes(db):
    mw = TermsMiddleware()
    handled = await _run(mw, FakeCb(4, "terms:accept"), db)
    assert handled == [True]


async def test_accepted_passes_and_cached(db):
    await db.upsert_user(5, None, None)
    await db.accept_terms(5)
    mw = TermsMiddleware()
    handled = await _run(mw, FakeMsg(5, "@x"), db)
    assert handled == [True]
    # второй вызов не должен ходить в БД (запись уже в памяти)
    assert 5 in mw._accepted


async def test_bot_events_skipped(db):
    mw = TermsMiddleware()
    ev = FakeMsg(0, "x")
    ev.from_user = SimpleNamespace(id=999, is_bot=True)
    handled = await _run(mw, ev, db)
    assert handled == [True]

async def test_admin_bypass(db):
    from app.config import settings

    original = settings.admin_ids
    object.__setattr__(settings, "admin_ids", frozenset({777}))
    try:
        mw = TermsMiddleware()
        handled = await _run(mw, FakeMsg(777, "anything"), db)
        assert handled == [True]
    finally:
        object.__setattr__(settings, "admin_ids", original)


async def test_accept_terms_upsert_without_row(db):
    # согласие сохраняется даже если строки пользователя ещё нет
    assert await db.has_accepted_terms(321) is False
    await db.accept_terms(321)
    assert await db.has_accepted_terms(321) is True
