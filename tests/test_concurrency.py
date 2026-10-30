"""Задача 3: параллельные поиски уважают MAX_HEAVY_SEARCHES."""
import asyncio
from types import SimpleNamespace

from app.config import settings
from app.services import search_runner
from app.services.search_runner import SearchSpec


class FakeBot:
    async def send_message(self, *a, **kw):
        pass


class FakeMessage:
    def __init__(self, uid):
        self.from_user = SimpleNamespace(id=uid)
        self.bot = FakeBot()

    async def answer(self, text, **kw):
        return self

    async def answer_document(self, doc, **kw):
        return self

    async def delete(self):
        pass


def _slow_spec(started, finished):
    async def analyze(v):
        started.append(v)
        await asyncio.sleep(0.05)
        finished.append(v)
        return {"ok": True}

    return SearchSpec(
        kind="username",
        normalize=lambda v: v,
        validate=lambda v: True,
        analyze=analyze,
        build_report=lambda f, v, lang: b"x",
        filename_prefix="t",
    )


async def test_heavy_limit(db, monkeypatch):
    object.__setattr__(settings, "max_heavy_searches", 2)
    search_runner._heavy_sem = None  # пересоздать под новый лимит
    started: list = []
    finished: list = []
    spec = _slow_spec(started, finished)

    users = list(range(1000, 1020))
    for uid in users:
        await db.upsert_user(uid, None, None)

    async def one(uid):
        # вызываем тяжёлый вариант напрямую
        msg = FakeMessage(uid)
        await search_runner.run_search_heavy(msg, db, "ru", spec, str(uid), user_id=uid)

    await asyncio.gather(*(one(u) for u in users))
    assert len(started) == 20
    assert len(finished) == 20


async def test_heavy_concurrency_never_exceeds(db, monkeypatch):
    object.__setattr__(settings, "max_heavy_searches", 2)
    search_runner._heavy_sem = None
    peak = {"now": 0, "max": 0}

    async def analyze(v):
        peak["now"] += 1
        peak["max"] = max(peak["max"], peak["now"])
        await asyncio.sleep(0.03)
        peak["now"] -= 1
        return {"ok": True}

    spec = SearchSpec(
        kind="email", normalize=lambda v: v, validate=lambda v: True,
        analyze=analyze, build_report=lambda f, v, l: b"x", filename_prefix="t",
    )
    users = list(range(2000, 2010))
    for uid in users:
        await db.upsert_user(uid, None, None)

    async def one(uid):
        await search_runner.run_search_heavy(
            FakeMessage(uid), db, "ru", spec, str(uid), user_id=uid
        )

    await asyncio.gather(*(one(u) for u in users))
    assert peak["max"] <= 2