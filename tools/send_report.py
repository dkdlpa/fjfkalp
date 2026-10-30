"""Отправляет итоговый отчёт и архив в Telegram.

Токен берётся из REPORT_BOT_TOKEN. Chat ID можно передать явно через
REPORT_CHAT_ID; иначе скрипт попробует найти последний чат в getUpdates.
Если апдейтов нет — ждёт до WAIT_SECONDS и завершается с кодом 2.
"""
from __future__ import annotations

import os
import sys
import time

import httpx

TOKEN = os.getenv("REPORT_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("REPORT_CHAT_ID", "").strip()
ZIP_PATH = os.getenv("REPORT_ZIP", "osint-bot-optimized.zip")
WAIT_SECONDS = int(os.getenv("REPORT_WAIT", "60"))
API = f"https://api.telegram.org/bot{TOKEN}"

REPORT = """<b>OSINT Bot — обновление готово</b>

Архив: <code>osint-bot-optimized.zip</code> (обновлён).

<b>1. Кнопки и нагрузка</b>
• <code>await callback.answer()</code> перенесён в самое начало каждого
  callback-хэндлера — ошибка «query is too old» больше не возникает.
• Один общий HTTP-клиент: пул соединений, DNS-кэш на 300 секунд,
  <code>asyncio.Semaphore(15)</code> и чтение страниц первыми 16 КБ.

<b>2. TikTok</b>
• Username, никнейм, био, верификация, SecUID, точная дата создания
  (createTime), статистика, аватар, язык и страна.
• Страна выводится с эмодзи-флагом и русским названием: например,
  🇷🇴 Румыния (RO).

<b>3. Никнейм</b>
• 52 площадки: СНГ, соцсети, IT и игры.
• Отчёт — чистый список активных ссылок по категориям, без Soft-404
  и без упоминания сторонних баз.

<b>4. IP и домены</b>
• Геолокация, регион, город, координаты, часовой пояс, провайдер, ASN.
• Проверка сетевой активности, открытые порты, хостнеймы и уязвимости.
• WHOIS/RDAP, DNS-записи, поддомены и ссылки на архивные копии.
• Новый блок «Справедливая сводка по открытым портам» с понятной
  расшифровкой каждого порта.

<b>5. Dork и веб-поиск</b>
• Поиск через документированный DuckDuckGo-эндпоинт: organic_results,
  knowledge_graph, top_stories, related_searches.
• Генератор выдаёт 25–35 запросов с site:, inurl:, intitle: и
  filetype: pdf/xlsx/docx/sql/txt/env/log.
• Длинные списки больше не уходят в чат: приходит HTML-отчёт с карточками
  результатов, синими кликабельными заголовками и серыми доменами.

<b>6. Тексты</b>
• Все информационные тексты переписаны в живом, понятном стиле.
• Справка «Что умеет этот бот» обновлена.

<b>Проверено</b>
<code>compileall</code>, импорт всех сервисов и роутеров, сборка HTML-отчётов,
реальные запросы к гео/портам/DNS, DNS-кэш и TLS через общий клиент,
проверка первого оператора в каждом callback-хэндлере.

Подробности — в <code>OPTIMIZATIONS.md</code> внутри архива."""


def get_chat_id(client: httpx.Client) -> int | None:
    if CHAT_ID:
        try:
            return int(CHAT_ID)
        except ValueError:
            print("REPORT_CHAT_ID is not an integer")
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            response = client.get(f"{API}/getUpdates", params={"limit": 50, "timeout": 0})
            data = response.json()
            for update in reversed(data.get("result") or []):
                for key in ("message", "edited_message", "channel_post"):
                    message = update.get(key)
                    if message and message.get("chat", {}).get("id"):
                        return int(message["chat"]["id"])
        except Exception as exc:  # noqa: BLE001
            print("getUpdates:", type(exc).__name__)
        time.sleep(3)
    return None


def main() -> int:
    if not TOKEN:
        print("REPORT_BOT_TOKEN is empty")
        return 1
    with httpx.Client(timeout=60) as client:
        chat_id = get_chat_id(client)
        if chat_id is None:
            print("NO_CHAT_ID: send /start to the bot first")
            return 2
        result = client.post(
            f"{API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": REPORT,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        print("sendMessage", result.status_code, result.json().get("ok"))
        if os.path.exists(ZIP_PATH):
            with open(ZIP_PATH, "rb") as file:
                result = client.post(
                    f"{API}/sendDocument",
                    data={"chat_id": str(chat_id), "caption": "osint-bot-optimized.zip"},
                    files={"document": ("osint-bot-optimized.zip", file, "application/zip")},
                )
            print("sendDocument", result.status_code, result.json().get("ok"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
