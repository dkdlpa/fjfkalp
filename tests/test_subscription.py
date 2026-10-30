"""Тесты SubscriptionService (без сети: get_chat_member замокан)."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from app.config import Channel, settings
from app.services import subscription


def _member(status, is_member=None):
    return SimpleNamespace(status=status, is_member=is_member)


def _exc(text):
    return Exception(text)


class FakeBot:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.id = 999
        self.sent = []

    async def get_chat_member(self, chat_id, user_id):
        self.calls.append((chat_id, user_id))
        result = self.handler(chat_id, user_id)
        if isinstance(result, BaseException):
            raise result
        if asyncio.iscoroutine(result):
            return await result
        return result

    async def get_chat(self, chat_id):
        return SimpleNamespace(member_count=100)

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)


@pytest.fixture
def service(monkeypatch):
    """Свежий сервис с 1 каналом и быстрыми таймаутами."""
    svc = subscription.SubscriptionService()
    svc._channels = (Channel("-1001111111111", "https://t.me/+aaa", "Канал 1"),)
    svc._links = {"-1001111111111": "https://t.me/+aaa"}
    object.__setattr__(settings, "subscription_cache_ttl", 60.0)
    object.__setattr__(settings, "subscription_check_timeout", 0.2)
    object.__setattr__(settings, "subscription_on_error", "grace")
    object.__setattr__(settings, "subscription_grace_seconds", 1800.0)
    object.__setattr__(settings, "admin_ids", frozenset({1}))
    return svc


class TestSubscribed:
    @pytest.mark.parametrize(
        "handler",
        [
            lambda c, u: _member("member"),
            lambda c, u: _member("administrator"),
            lambda c, u: _member("creator"),
            lambda c, u: _member("restricted", is_member=True),
        ],
    )
    async def test_ok(self, service, handler):
        res = await service.check(FakeBot(handler), 42)
        assert res.ok is True
        assert res.missing == []


class TestNotSubscribed:
    @pytest.mark.parametrize("status", ["left", "kicked"])
    async def test_left_kicked(self, service, status):
        res = await service.check(FakeBot(lambda c, u: _member(status)), 42)
        assert res.ok is False
        assert res.missing == ["-1001111111111"]
        assert res.missing_channels[0].title == "Канал 1"

    async def test_restricted_false(self, service):
        res = await service.check(
            FakeBot(lambda c, u: _member("restricted", is_member=False)), 42
        )
        assert res.ok is False

    async def test_user_not_found_is_not_member(self, service):
        res = await service.check(
            FakeBot(lambda c, u: _exc("Bad Request: user not found")), 42
        )
        assert res.ok is False
        assert res.missing


class TestConfigError:
    @pytest.mark.parametrize("mode", ["closed", "grace"])
    async def test_new_user_blocked(self, service, monkeypatch, mode):
        object.__setattr__(settings, "subscription_on_error", mode)
        res = await service.check(
            FakeBot(lambda c, u: _exc("Bad Request: chat not found")), 42
        )
        assert res.ok is False
        assert res.error == "config"

    async def test_grace_allows_recent(self, service):
        svc = service
        # свежая отметка, но кэш истёк по TTL -> идём на проверку, получаем ошибку
        svc._cache[42] = time.monotonic() - settings.subscription_cache_ttl - 1
        res = await svc.check(
            FakeBot(lambda c, u: _exc("Bad Request: chat not found")), 42
        )
        assert res.ok is True
        assert res.stale_ok is True

    async def test_admin_alert_throttled(self, service, monkeypatch):
        from app.services import bot_ref

        bot = FakeBot(lambda c, u: _exc("Bad Request: chat not found"))
        bot_ref.set_bot(bot)
        try:
            await service.check(bot, 1, force=True)
            await service.check(bot, 2, force=True)
        finally:
            bot_ref.set_bot(None)
        # второй алерт по тому же каналу подавлен (30 минут)
        assert len(bot.sent) == 1


class TestTemporary:
    async def test_timeout_retry_then_grace(self, service, monkeypatch):
        calls = {"n": 0}

        def handler(c, u):
            calls["n"] += 1
            raise asyncio.TimeoutError()

        # ускоряем повтор
        monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
        res = await service.check(FakeBot(handler), 42)
        # одна повторная попытка => 2 вызова
        assert calls["n"] == 2
        assert res.ok is False
        assert res.error == "temporary"

    async def test_retry_after_retries(self, service, monkeypatch, caplog):
        from aiogram.exceptions import TelegramRetryAfter

        calls = {"n": 0}

        def handler(c, u):
            calls["n"] += 1
            return TelegramRetryAfter(method=None, message="flood", retry_after=1)

        monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
        res = await service.check(FakeBot(handler), 42)
        assert calls["n"] == 2
        assert res.ok is False


class TestCache:
    async def test_cache_avoids_calls(self, service):
        bot = FakeBot(lambda c, u: _member("member"))
        await service.check(bot, 42)
        await service.check(bot, 42)
        assert len(bot.calls) == 1

    async def test_force_bypasses_cache(self, service):
        bot = FakeBot(lambda c, u: _member("member"))
        await service.check(bot, 42)
        await service.check(bot, 42, force=True)
        assert len(bot.calls) == 2

    async def test_left_event_drops_access(self, service):
        bot = FakeBot(lambda c, u: _member("member"))
        assert (await service.check(bot, 42)).ok is True
        service.set_member_status(42, "left")
        res = await service.check(FakeBot(lambda c, u: _member("left")), 42)
        assert res.ok is False


class TestParallel:
    async def test_channels_checked_in_parallel(self, service):
        svc = service
        svc._channels = tuple(
            Channel(f"-100111111111{i}", "", f"Канал {i}") for i in range(3)
        )

        async def slow(c, u):
            await asyncio.sleep(0.1)
            return _member("member")

        start = time.monotonic()
        res = await svc.check(FakeBot(slow), 42)
        elapsed = time.monotonic() - start
        assert res.ok
        # последовательно было бы ~0.3с; параллельно ~0.1с
        assert elapsed < 0.25


class TestNoChannels:
    async def test_disabled_passes(self):
        svc = subscription.SubscriptionService()
        svc._channels = ()
        res = await svc.check(FakeBot(lambda c, u: _member("left")), 42)
        assert res.ok is True
        assert svc.enabled is False


class TestConfigNumbering:
    def test_gap_numbering(self, monkeypatch):
        for key in list(["PRIVATE_CHANNEL_ID", "PRIVATE_CHANNEL_LINK"]):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("PRIVATE_CHANNEL_ID_2", "-1002222222222")
        monkeypatch.setenv("PRIVATE_CHANNEL_LINK_2", "t.me/+xxxx")
        from app.config import _read_private_channels

        pairs = _read_private_channels()
        assert ("-1002222222222", "https://t.me/+xxxx") in pairs


async def _fast_sleep(*a, **kw):
    return None

class TestRunSearchGate:
    async def test_no_charge_when_subscription_lost(self, db, service, monkeypatch):
        from types import SimpleNamespace

        from app.services import search_runner
        from app.services.search_runner import SearchSpec
        from app.services import subscription as sub_mod

        # сервис считает, что пользователь НЕ подписан
        object.__setattr__(settings, "admin_ids", frozenset())
        monkeypatch.setattr(sub_mod, "service", service)
        monkeypatch.setattr(
            service, "check", _stub_check_not_ok
        )

        uid = 77
        await db.upsert_user(uid, None, None)
        before = await db.trial_state(uid)

        class Msg:
            from_user = SimpleNamespace(id=uid)
            bot = object()

            async def answer(self, text, **kw):
                return self

            async def answer_document(self, *a, **kw):
                return self

            async def delete(self):
                pass

        spec = SearchSpec(
            kind="phone", normalize=lambda v: v, validate=lambda v: True,
            analyze=lambda v: {"ok": True}, build_report=lambda f, v, l: b"x",
        )
        await search_runner.run_search(Msg(), db, "ru", spec, "x", user_id=uid)
        after = await db.trial_state(uid)
        assert after["used"] == before["used"]  # не списано


async def _stub_check_not_ok(bot, user_id, force=False):
    return subscription.SubResult(ok=False, missing=["-1001111111111"], missing_channels=[
        subscription.Channel("-1001111111111", "", "Канал 1")
    ])
