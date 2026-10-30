"""Слой данных: SQLite через aiosqlite (WAL).

Конкурентность: одно соединение + asyncio.Lock вокруг всех write-операций.
Составные операции выполняются в транзакции BEGIN IMMEDIATE ... COMMIT/ROLLBACK,
поэтому commit посторонней корутины не может «зафиксировать» чужую половину
операции, а rollback не стирает чужое начисление.

Идентификация в логах: только user_id и короткий хэш, без PII.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections import OrderedDict
from contextlib import asynccontextmanager

import aiosqlite

from app.config import settings

log = logging.getLogger(__name__)

_MISSING = object()
_CACHE_LIMIT = 10_000


def query_hash(value: str) -> str:
    """Короткий хэш запроса для логов/ключей без раскрытия PII."""
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()[:12]


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        language TEXT,
        is_blocked INTEGER DEFAULT 0,
        disclaimer_accepted INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        searches_count INTEGER DEFAULT 0,
        requests_bonus INTEGER DEFAULT 0,
        credits INTEGER DEFAULT 0,
        trial_used INTEGER DEFAULT 0,
        trial_started TEXT,
        referred_by INTEGER,
        referral_bonused INTEGER DEFAULT 0,
        total_paid_cents INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS payments (
        invoice_id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        package_id TEXT NOT NULL,
        amount TEXT NOT NULL,
        asset TEXT NOT NULL,
        credits INTEGER NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        paid_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        credits INTEGER NOT NULL,
        meta TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        kind TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS errors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        context TEXT,
        message TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS admin_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        meta TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS search_cache (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    # Аддитивная таблица агрегированных счётчиков модулей.
    # Создаётся при первом запуске новой версии; существующие таблицы и
    # данные не затрагиваются. Хранит только числа, без запросов/отчётов.
    """
    CREATE TABLE IF NOT EXISTS module_stats (
        kind TEXT PRIMARY KEY,
        count INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_search_cache_time ON search_cache(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_requests_user_time ON requests(user_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_payments_user_status ON payments(user_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_transactions_kind ON transactions(user_id, kind, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_users_referred_by ON users(referred_by)",
)


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None
        self._lang_cache: OrderedDict[int, str | None] = OrderedDict()
        self._settings_cache: dict[str, object] = {}
        # Единая блокировка записи: сериализует транзакции на одном соединении.
        self._write_lock = asyncio.Lock()

    # ---------- lifecycle ----------
    async def connect(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        await self.conn.execute("PRAGMA busy_timeout=5000")
        await self.conn.execute("PRAGMA foreign_keys=ON")
        await self.conn.execute("PRAGMA temp_store=MEMORY")
        await self.conn.execute("PRAGMA cache_size=-4000")
        await self.conn.execute("PRAGMA journal_size_limit=16777216")
        for stmt in _SCHEMA:
            await self.conn.execute(stmt)
        await self.conn.commit()
        # миграция для существующих БД
        await self._ensure_column("users", "requests_bonus", "INTEGER DEFAULT 0")
        await self._ensure_column("users", "credits", "INTEGER DEFAULT 0")
        await self._ensure_column("users", "trial_used", "INTEGER DEFAULT 0")
        await self._ensure_column("users", "trial_started", "TEXT")
        await self._ensure_column("users", "referred_by", "INTEGER")
        await self._ensure_column("users", "referral_bonused", "INTEGER DEFAULT 0")
        await self._ensure_column("users", "total_paid_cents", "INTEGER DEFAULT 0")
        await self._ensure_column("users", "disclaimer_accepted", "INTEGER DEFAULT 0")

    @asynccontextmanager
    async def transaction(self):
        """Транзакция под блокировкой записи.

        BEGIN IMMEDIATE берёт блокировку записи сразу, поэтому два писателя
        не пересекаются, а commit/rollback касаются только своей операции.
        """
        async with self._write_lock:
            assert self.conn is not None
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                await self.conn.rollback()
                raise
            else:
                await self.conn.commit()

    async def _write(self, sql: str, params: tuple = ()) -> int:
        """Одиночная запись в своей мини-транзакции. Возвращает rowcount."""
        async with self.transaction() as conn:
            cur = await conn.execute(sql, params)
            return cur.rowcount

    async def prune(self, retention_days: int = 2) -> None:
        """Удаляет старые запросы/ошибки/кэш, затем усекает WAL."""
        try:
            days = f"-{max(1, retention_days)} day"
            async with self.transaction() as conn:
                await conn.execute("DELETE FROM requests WHERE created_at < datetime('now', ?)", (days,))
                await conn.execute("DELETE FROM errors WHERE created_at < datetime('now', ?)", (days,))
                await conn.execute(
                    "DELETE FROM search_cache WHERE created_at < datetime('now', '-1 day')"
                )
            assert self.conn is not None
            await self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:  # noqa: BLE001
            log.exception("Не удалось очистить старые записи")

    async def cleanup(self) -> None:
        """Полная очистка: ошибки, старые запросы, VACUUM (для админ-панели)."""
        try:
            async with self.transaction() as conn:
                await conn.execute("DELETE FROM errors")
                await conn.execute(
                    "DELETE FROM requests WHERE created_at < datetime('now', '-2 day')"
                )
            assert self.conn is not None
            await self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            await self.conn.execute("VACUUM")
        except Exception:  # noqa: BLE001
            log.exception("Не удалось выполнить очистку БД")

    async def _ensure_column(self, table: str, column: str, decl: str) -> None:
        async with self.conn.execute(f"PRAGMA table_info({table})") as cur:
            rows = await cur.fetchall()
        if column not in {r["name"] for r in rows}:
            await self._write(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    async def close(self) -> None:
        if self.conn:
            conn, self.conn = self.conn, None
            await conn.close()

    # ---------- users ----------
    async def upsert_user(self, uid: int, username: str | None, first_name: str | None) -> None:
        """Создаёт обезличенную запись (Zero-PII: name/username не пишем)."""
        await self._write(
            "INSERT INTO users (user_id, username, first_name) VALUES (?, NULL, NULL) "
            "ON CONFLICT(user_id) DO NOTHING",
            (uid,),
        )

    async def user_exists(self, uid: int) -> bool:
        async with self.conn.execute("SELECT 1 FROM users WHERE user_id = ?", (uid,)) as cur:
            return await cur.fetchone() is not None

    async def get_user_profile(self, uid: int) -> dict | None:
        async with self.conn.execute("SELECT * FROM users WHERE user_id = ?", (uid,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def increment_searches(self, uid: int) -> None:
        await self._write(
            "UPDATE users SET searches_count = COALESCE(searches_count, 0) + 1 WHERE user_id = ?",
            (uid,),
        )

    def _cache_lang(self, uid: int, value: str | None) -> None:
        self._lang_cache[uid] = value
        self._lang_cache.move_to_end(uid)
        if len(self._lang_cache) > _CACHE_LIMIT:
            self._lang_cache.popitem(last=False)

    async def get_user_language(self, uid: int) -> str | None:
        if uid in self._lang_cache:
            self._lang_cache.move_to_end(uid)
            return self._lang_cache[uid]
        async with self.conn.execute("SELECT language FROM users WHERE user_id = ?", (uid,)) as cur:
            row = await cur.fetchone()
        lang = row["language"] if row and row["language"] in {"ru", "en"} else None
        self._cache_lang(uid, lang)
        return lang

    async def set_user_language(self, uid: int, language: str) -> None:
        await self._write("UPDATE users SET language = ? WHERE user_id = ?", (language, uid))
        self._cache_lang(uid, language)

    async def set_blocked(self, uid: int, blocked: bool) -> None:
        await self._write(
            "UPDATE users SET is_blocked = ? WHERE user_id = ?", (int(blocked), uid)
        )

    # ---------- consent ----------
    async def has_accepted_terms(self, uid: int) -> bool:
        async with self.conn.execute(
            "SELECT disclaimer_accepted FROM users WHERE user_id = ?", (uid,)
        ) as cur:
            row = await cur.fetchone()
        return bool(row and row["disclaimer_accepted"])

    async def accept_terms(self, uid: int) -> None:
        """Создаёт запись при необходимости и фиксирует согласие."""
        async with self.transaction() as conn:
            await conn.execute(
                "INSERT INTO users (user_id, disclaimer_accepted) VALUES (?, 1) "
                "ON CONFLICT(user_id) DO UPDATE SET disclaimer_accepted = 1",
                (uid,),
            )

    async def get_all_user_ids(self) -> list[int]:
        async with self.conn.execute("SELECT user_id FROM users WHERE is_blocked = 0") as cur:
            rows = await cur.fetchall()
        return [r["user_id"] for r in rows]

    async def count_active_users(self) -> int:
        async with self.conn.execute("SELECT COUNT(*) AS c FROM users WHERE is_blocked = 0") as cur:
            row = await cur.fetchone()
        return int(row["c"]) if row else 0

    async def iter_user_ids(self, batch_size: int = 500):
        """Батчевый итератор для рассылки — не держит весь список в памяти."""
        async with self.conn.execute("SELECT user_id FROM users WHERE is_blocked = 0") as cur:
            while True:
                rows = await cur.fetchmany(batch_size)
                if not rows:
                    return
                for row in rows:
                    yield row["user_id"]

    # ---------- requests / quota ----------
    async def record_search(self, uid: int, kind: str = "") -> None:
        async with self.transaction() as conn:
            await conn.execute("INSERT INTO requests (user_id, kind) VALUES (?, ?)", (uid, kind))
            await conn.execute(
                "UPDATE users SET searches_count = COALESCE(searches_count, 0) + 1 WHERE user_id = ?",
                (uid,),
            )

    async def increment_request(self, uid: int, kind: str = "") -> None:
        await self._write("INSERT INTO requests (user_id, kind) VALUES (?, ?)", (uid, kind))

    async def get_requests_today(self, uid: int) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) AS c FROM requests "
            "WHERE user_id = ? AND created_at >= date('now')",
            (uid,),
        ) as cur:
            row = await cur.fetchone()
        return int(row["c"]) if row else 0

    # ---------- credits, пробный баланс ----------
    async def get_credits(self, uid: int) -> int:
        async with self.conn.execute("SELECT credits FROM users WHERE user_id = ?", (uid,)) as cur:
            row = await cur.fetchone()
        return int(row["credits"] or 0) if row else 0

    async def add_credits(self, uid: int, amount: int, kind: str, meta: str | None = None) -> int:
        """Начисляет запросы, пишет ФАКТИЧЕСКОЕ изменение и возвращает баланс."""
        async with self.transaction() as conn:
            await conn.execute(
                "INSERT INTO users (user_id) VALUES (?) ON CONFLICT(user_id) DO NOTHING",
                (uid,),
            )
            async with conn.execute(
                "SELECT COALESCE(credits, 0) AS c FROM users WHERE user_id = ?", (uid,)
            ) as cur:
                row = await cur.fetchone()
            before = int(row["c"]) if row else 0
            after = max(0, before + int(amount))
            applied = after - before
            await conn.execute("UPDATE users SET credits = ? WHERE user_id = ?", (after, uid))
            await conn.execute(
                "INSERT INTO transactions (user_id, kind, credits, meta) VALUES (?, ?, ?, ?)",
                (uid, kind, applied, meta),
            )
            return after

    async def trial_state(self, uid: int) -> dict:
        """Состояние пробного периода: использовано, начат ли, истек ли, конец."""
        async with self.conn.execute(
            "SELECT COALESCE(trial_used, 0) AS used, trial_started, "
            "CASE WHEN trial_started IS NOT NULL THEN datetime(trial_started, ?) END AS ends_at "
            "FROM users WHERE user_id = ?",
            (f"+{settings.trial_hours} hour", uid),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return {"used": 0, "started": None, "expired": False, "ends_at": None}
        used = int(row["used"])
        started = row["trial_started"]
        async with self.conn.execute(
            "SELECT (? < datetime('now')) AS expired", (row["ends_at"] or "9999-12-31",)
        ) as cur:
            r = await cur.fetchone()
        expired = bool(r["expired"]) if r else False
        return {
            "used": used,
            "started": started,
            "expired": expired,
            "ends_at": row["ends_at"],
        }

    async def trial_available(self, uid: int) -> bool:
        st = await self.trial_state(uid)
        return not st["expired"] and st["used"] < settings.trial_requests

    async def spend_credit(self, uid: int, kind: str = "") -> str | None:
        """Атомарно списывает запрос: сначала пробные, затем оплаченные.

        Возвращает "trial"/"credit" или None, если баланс пуст. Всё в одной
        транзакции: два параллельных вызова не уведут баланс в минус.
        """
        try:
            return await self._spend_txn(uid, kind)
        except _EmptyBalance:
            return None

    async def _spend_txn(self, uid: int, kind: str) -> str | None:
        async with self.transaction() as conn:
            await conn.execute(
                "INSERT INTO users (user_id) VALUES (?) ON CONFLICT(user_id) DO NOTHING",
                (uid,),
            )
            cur = await conn.execute(
                "UPDATE users SET trial_used = COALESCE(trial_used, 0) + 1, "
                "trial_started = COALESCE(trial_started, datetime('now')) "
                "WHERE user_id = ? AND COALESCE(trial_used, 0) < ? "
                "AND (trial_started IS NULL OR trial_started >= datetime('now', ?))",
                (uid, settings.trial_requests, f"-{settings.trial_hours} hour"),
            )
            if cur.rowcount == 1:
                spend_kind = "trial"
            else:
                cur = await conn.execute(
                    "UPDATE users SET credits = credits - 1 WHERE user_id = ? AND credits > 0",
                    (uid,),
                )
                if cur.rowcount != 1:
                    raise _EmptyBalance()
                spend_kind = "credit"
            await conn.execute("INSERT INTO requests (user_id, kind) VALUES (?, ?)", (uid, kind))
            await conn.execute(
                "UPDATE users SET searches_count = COALESCE(searches_count, 0) + 1 WHERE user_id = ?",
                (uid,),
            )
            await conn.execute(
                "INSERT INTO transactions (user_id, kind, credits) VALUES (?, ?, ?)",
                (uid, f"spend_{spend_kind}", -1),
            )
            return spend_kind

    async def get_credits_and_paid(self, uid: int) -> tuple[int, int]:
        async with self.conn.execute(
            "SELECT COALESCE(credits, 0) AS credits, COALESCE(total_paid_cents, 0) AS paid "
            "FROM users WHERE user_id = ?",
            (uid,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return 0, 0
        return int(row["credits"]), int(row["paid"])

    async def get_referral_stats(self, uid: int) -> tuple[int, int]:
        """Приглашенные и суммарно начислено запросов за рефералов."""
        async with self.conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE referred_by = ?", (uid,)
        ) as cur:
            row = await cur.fetchone()
        count = int(row["c"]) if row else 0
        async with self.conn.execute(
            "SELECT COALESCE(SUM(credits), 0) AS total FROM transactions "
            "WHERE user_id = ? AND kind = 'referral'",
            (uid,),
        ) as cur:
            row = await cur.fetchone()
        total = int(row["total"]) if row else 0
        return count, total

    async def referral_bonuses_today(self, referrer_id: int) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) AS c FROM transactions "
            "WHERE user_id = ? AND kind = 'referral' AND created_at >= date('now')",
            (referrer_id,),
        ) as cur:
            row = await cur.fetchone()
        return int(row["c"]) if row else 0

    async def give_all(self, amount: int, kind: str = "admin_giveall") -> int:
        """Пакетное начисление всем пользователям. Возвращает число получателей."""
        async with self.transaction() as conn:
            await conn.execute(
                "UPDATE users SET credits = MAX(0, COALESCE(credits, 0) + ?)", (int(amount),)
            )
            await conn.execute(
                "INSERT INTO transactions (user_id, kind, credits) "
                "SELECT user_id, ?, ? FROM users",
                (kind, int(amount)),
            )
            async with conn.execute("SELECT COUNT(*) AS c FROM u
