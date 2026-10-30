"""Фоновая проверка статусов счетов CryptoPay (поллинг вместо вебхука).

Вебхук требует публичный HTTPS-домен и отдельный aiohttp-сервер; для
одиночного бота дешевле и надёжнее поллинг:
- берём из БД только счета в статусе 'active';
- пакетно спрашиваем статусы в CryptoPay;
- 'paid' -> начисляем запросы и уведомляем пользователя;
- 'expired' -> помечаем cancelled, чтобы не опрашивать вечно.

Начисление идемпотентно (db.mark_payment_paid), поэтому гонки с вебхуком
или рестартом не приводят к двойному зачислению.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from app.config import settings
from app.db import Database
from app.i18n import t
from app.services import billing, cryptopay

log = logging.getLogger(__name__)


async def poll_once(bot: Bot, db: Database) -> int:
    """Один проход по активным счетам. Возвращает число подтверждённых оплат."""
    pending = await db.get_pending_payments()
    if not pending:
        return 0

    ids = [int(p["invoice_id"]) for p in pending]
    try:
        invoices = await cryptopay.get_invoices(ids)
    except cryptopay.CryptoPayError as exc:
        log.warning("CryptoPay поллинг: %s", type(exc).__name__)
        return 0

    by_id = {int(i["invoice_id"]): i for i in invoices if "invoice_id" in i}
    confirmed = 0
    for payment in pending:
        invoice = by_id.get(int(payment["invoice_id"]))
        if invoice is None:
            continue
        status = str(invoice.get("status"))
        if status == "paid":
            result = await billing.confirm_payment(db, invoice)
            if result is None:
                continue
            confirmed += 1
            await _notify_paid(bot, db, result)
        elif status in {"expired", "cancelled"}:
            await db.cancel_payment(int(payment["invoice_id"]))
    return confirmed


async def _notify_paid(bot: Bot, db: Database, payment: dict) -> None:
    uid = int(payment["user_id"])
    language = (await db.get_user_language(uid)) or settings.default_language
    balance = await db.get_credits(uid)
    try:
        await bot.send_message(
            uid,
            t(language, "pay_success", credits=payment["credits"], balance=balance),
        )
    except Exception:  # noqa: BLE001 — пользователь мог заблокировать бота
        pass


async def poll_loop(bot: Bot, db: Database) -> None:
    interval = max(5.0, settings.payment_poll_interval)
    while True:
        try:
            await asyncio.sleep(interval)
            await poll_once(bot, db)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            log.exception("Ошибка поллинга платежей")