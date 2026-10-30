"""Биллинг: пакеты запросов, выставление счетов, начисление и рефералы.

Единый источник правды о цене и количестве запросов — PACKAGES.
Все операции начисления идемпотентны на уровне БД (payments/invoice_id).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import settings
from app.db import Database
from app.services import cryptopay

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Package:
    id: str
    credits: int
    amount: str  # цена в USD строкой — так её ждёт CryptoPay

    @property
    def cents(self) -> int:
        return int(round(float(self.amount) * 100))


PACKAGES: dict[str, Package] = {
    p.id: p
    for p in (
        Package(id="p10", credits=10, amount="0.20"),
        Package(id="p50", credits=50, amount="0.50"),
        Package(id="p100", credits=100, amount="1.50"),
    )
}


def get_package(package_id: str) -> Package | None:
    return PACKAGES.get(package_id)


def per_request_price(pkg: Package) -> str:
    return f"{float(pkg.amount) / pkg.credits:.3f}"


async def create_invoice_for(db: Database, uid: int, package_id: str) -> dict:
    """Выставляет счёт и регистрирует его как pending. Возвращает Invoice."""
    pkg = PACKAGES[package_id]
    invoice = await cryptopay.create_invoice(
        amount=pkg.amount,
        description=f"{pkg.credits} OSINT requests",
        payload=f"{uid}:{package_id}",
        expires_in=settings.invoice_ttl_minutes * 60,
    )
    invoice_id = int(invoice["invoice_id"])
    args = (invoice_id, uid, package_id, pkg.amount, str(invoice.get("asset") or "USDT"), pkg.credits)
    ok = False
    for attempt in range(2):
        try:
            ok = await db.create_payment(*args, status="active")
            break
        except Exception:  # noqa: BLE001
            log.exception("Не удалось сохранить счёт %s (попытка %s)", invoice_id, attempt + 1)
    if not ok and await db.get_payment(invoice_id) is None:
        log.error("Счёт %s создан в CryptoPay, но не сохранён в БД", invoice_id)
    return invoice


async def confirm_payment(db: Database, invoice: dict) -> dict | None:
    """Начисляет запросы за оплаченный счёт. Возвращает платёж или None.

    Идемпотентно: повторный вызов на тот же invoice_id ничего не начислит.
    """
    if str(invoice.get("status")) != "paid":
        return None
    try:
        invoice_id = int(invoice["invoice_id"])
    except (KeyError, TypeError, ValueError):
        return None
    payment = await db.mark_payment_paid(invoice_id, "paid")
    if payment is None:
        return None
    log.info(
        "Оплата: user=%s invoice=%s credits=%s",
        payment["user_id"],
        invoice_id,
        payment["credits"],
    )
    return payment


async def register_referral(db: Database, new_uid: int, referrer_id: int) -> bool:
    """Привязывает реферала. Возвращает True, если привязка создана впервые."""
    if referrer_id <= 0 or referrer_id == new_uid:
        return False
    return await db.set_referrer(new_uid, referrer_id)


async def grant_referral_bonus(db: Database, new_uid: int) -> int | None:
    """Начисляет рефереру бонус один раз (после первого успешного списания).

    claim_referral_bonus делает проверку идемпотентности, суточный лимит
    и само начисление в одной транзакции.
    """
    if settings.referral_bonus <= 0:
        return None
    referrer_id = await db.claim_referral_bonus(new_uid)
    if referrer_id is None:
        return None
    log.info(
        "Реферал: referrer=%s user=%s (+%s)",
        referrer_id,
        new_uid,
        settings.referral_bonus,
    )
    return referrer_id