"""Запуск: main импортируется, _report_channel_diags устойчив к входам."""
import logging
from dataclasses import dataclass

import pytest


@dataclass
class _Diag:
    title: str
    found: bool
    bot_is_admin: bool
    note: str = ""


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


def test_main_imports():
    import main  # noqa: F401

    assert hasattr(main, "_report_channel_diags")


async def test_report_empty(caplog):
    import main

    bot = _Bot()
    with caplog.at_level(logging.INFO):
        await main._report_channel_diags(bot, [])
    assert bot.sent == []


async def test_report_none(caplog):
    import main

    bot = _Bot()
    await main._report_channel_diags(bot, None)
    assert bot.sent == []


async def test_report_problem_alerts_admins(caplog, monkeypatch):
    import main
    from app.config import settings

    object.__setattr__(settings, "admin_ids", frozenset({1, 2}))
    bot = _Bot()
    diags = [
        _Diag("Канал 1", found=True, bot_is_admin=True, note="ок"),
        _Diag("Канал 2", found=False, bot_is_admin=False, note="канал не найден"),
    ]
    with caplog.at_level(logging.ERROR):
        await main._report_channel_diags(bot, diags)
    # одно сообщение каждому админу
    assert len(bot.sent) == 2
    text = bot.sent[0][1]
    assert "Канал 2" in text
    assert "администратором" in text
    # в логе ошибка по проблемному каналу
    assert any(r.levelno == logging.ERROR and "Канал 2" in r.message for r in caplog.records)


async def test_report_all_ok_no_alert():
    import main

    bot = _Bot()
    await main._report_channel_diags(
        bot, [_Diag("Канал 1", found=True, bot_is_admin=True, note="ок")]
    )
    assert bot.sent == []