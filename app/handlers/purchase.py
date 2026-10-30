"""Покупка запросов через CryptoBot: меню пакетов, счёт, проверка оплаты.

Правило: на каждый callback.answer ровно один вызов в конце ветки.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.db import Database
from app.handlers.user import lang_of, show
from app.i18n import t
from app.keyboards import get_invoice_menu, get_packages_menu
from app.services import billing, cryptopay

log = logging.getLogger(__name__)
router = Router()

_PACKAGE_LIST = [(p.id, p.credits, p.amount) for p in billing.PACKAGES.values()]


def _format_price(amount: str) -> str:
    return f"{float(amount):.2f}"


@router.callback_query(F.data.in_({"buy:info", "buy:menu"}))
async def cb_packages(callback: CallbackQuery, db: Database):
    """Меню пакетов. Тексты и цены собираются из billing.PACKAGES."""
    language = await lang_of(db, callback.from_user.id)
    if not settings.payments_ready:
        await callback.answer(t(language, "pay_unavailable"), show_alert=True)
        return
    lines = [t(language, "buy_title"), ""]
    for pkg in billing.PACKAGES.values():
        lines.append(t(language, "buy_line", credits=pkg.credits, price=_format_price(pkg.amount)))
    packages = [(pid, credits, _format_price(amount)) for pid, credits, amount in _PACKAGE_LIST]
    await callback.answer()
    await show(callback, "\n".join(lines), get_packages_menu(language, packages))


@router.callback_query(F.data.startswith("pay:check:"))
async def cb_check_payment(callback: CallbackQuery, db: Database):
    language = await lang_of(db, callback.from_user.id)
    try:
        invoice_id = int(callback.data.split(":", 2)[2])
    except (IndexError, ValueError):
        await callback.answer(t(language, "pay_not_found"), show_alert=True)
        return

    payment = await db.get_payment(invoice_id)
    if payment is None or int(payment["user_id"]) != callback.from_user.id:
        await callback.answer(t(language, "pay_not_found"), show_alert=True)
        return
    if payment["status"] == "paid":
        await callback.answer(t(language, "pay_already"), show_alert=True)
        return

    try:
        invoice = await cryptopay.get_invoice(invoice_id)
    except cryptopay.CryptoPayError:
        await callback.answer(t(language, "pay_failed"), show_alert=True)
        return
    if not invoice:
        await callback.answer(t(language, "pay_not_found"), show_alert=True)
        return

    status = str(invoice.get("status"))
    if status == "paid":
        paid = await billing.confirm_payment(db, invoice)
        if paid is not None:
            balance = await db.get_credits(callback.from_user.id)
            await callback.answer(
                t(language, "pay_success", credits=paid["credits"], balance=balance),
                show_alert=True,
            )
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                pass
        else:
            await callback.answer(t(language, "pay_already"), show_alert=True)
        return
    if status in {"expired", "cancelled"}:
        await db.cancel_payment(invoice_id)
        await callback.answer(t(language, "pay_expired"), show_alert=True)
        return
    await callback.answer(t(language, "pay_pending"), show_alert=True)


@router.callback_query(F.data.startswith("pay:"))
async def cb_buy_package(callback: CallbackQuery, db: Database):
    language = await lang_of(db, callback.from_user.id)
    package_id = callback.data.split(":", 1)[1]
    if billing.get_package(package_id) is None:
        await callback.answer(t(language, "pay_failed"), show_alert=True)
        return
    if not settings.payments_ready:
        await callback.answer(t(language, "pay_unavailable"), show_alert=True)
        return

    try:
        invoice = await billing.create_invoice_for(db, callback.from_user.id, package_id)
    except cryptopay.CryptoPayError:
        log.warning("CryptoPay: не удалось создать счёт для %s", callback.from_user.id)
        await callback.answer(t(language, "pay_failed"), show_alert=True)
        return

    url = invoice.get("bot_invoice_url") or invoice.get("mini_app_invoice_url")
    if not url:
        await callback.answer(t(language, "pay_failed"), show_alert=True)
        return
    pkg = billing.get_package(package_id)
    text = t(language, "pay_invoice", credits=pkg.credits, price=_format_price(pkg.amount))
    markup = get_invoice_menu(language, str(url), int(invoice["invoice_id"]))
    await callback.answer()
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest:
            pass
    await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)