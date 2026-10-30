"""Тесты кэш-ключей и биллинга/БД (B2, B3, B6, D3)."""
import asyncio

import pytest

from app.services import billing, cache


class TestCacheKey:
    def test_stable_and_normalized(self):
        k1 = cache.make_key("email", "User@Example.com")
        k2 = cache.make_key("email", "user@example.com ")
        assert k1 == k2

    def test_no_pii_in_key(self):
        key = cache.make_key("phone", "+7 999 000-00-00")
        assert "phone:" in key
        # исходный номер не хранится в ключе открытым текстом
        assert "999" not in key
        assert key != cache.make_key("phone", "9990000000")

    def test_domain_normalized(self):
        assert cache.make_key("ip", "https://Example.com/path") == cache.make_key("ip", "example.com")


class TestBilling:
    async def test_payment_idempotent(self, db):
        await db.upsert_user(1, None, None)
        assert await db.create_payment(10, 1, "p50", "0.50", "USDT", 50)
        first = await db.mark_payment_paid(10)
        assert first is not None and first["credits"] == 50
        # повторный вызов ничего не начисляет
        assert await db.mark_payment_paid(10) is None
        assert await db.get_credits(1) == 50

    async def test_concurrent_payments_no_double(self, db):
        await db.upsert_user(2, None, None)
        await db.create_payment(20, 2, "p10", "0.20", "USDT", 10)
        results = await asyncio.gather(*(db.mark_payment_paid(20) for _ in range(10)))
        assert sum(r is not None for r in results) == 1
        assert await db.get_credits(2) == 10

    async def test_concurrent_spend(self, db):
        await db.upsert_user(3, None, None)
        # отключаем пробный баланс, чтобы проверить именно оплаченные кредиты
        st = await db.trial_state(3)
        await db._write("UPDATE users SET trial_used = ? WHERE user_id = 3", (10_000,))
        await db.add_credits(3, 5, "admin")
        results = await asyncio.gather(*(db.spend_credit(3, "x") for _ in range(10)))
        spent = [r for r in results if r is not None]
        assert len(spent) == 5
        assert await db.get_credits(3) == 0

    async def test_add_credits_records_applied(self, db):
        await db.upsert_user(4, None, None)
        await db.add_credits(4, 5, "admin")
        await db.add_credits(4, -100, "admin")
        # фактически применено -5, не -100
        txns = await db.get_transactions(4)
        assert txns[0]["credits"] == -5
        assert await db.get_credits(4) == 0

    async def test_referral_daily_limit(self, db):
        await db.upsert_user(100, None, None)
        for uid in range(200, 220):
            await db.upsert_user(uid, None, None)
            await db.set_referrer(uid, 100)
            await db.claim_referral_bonus(uid)
        # лимит REFERRAL_DAILY_LIMIT (по умолчанию 10)
        from app.config import settings

        count, _ = await db.get_referral_stats(100)
        assert count == 20
        assert await db.get_credits(100) == settings.referral_daily_limit * settings.referral_bonus