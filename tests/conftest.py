"""Общие фикстуры тестов: временная БД и настройки окружения."""
import os

import pytest_asyncio

os.environ.setdefault("BOT_TOKEN", "1:test")


@pytest_asyncio.fixture
async def db(tmp_path):
    from app.db import Database

    database = Database(str(tmp_path / "test.db"))
    await database.connect()
    yield database
    await database.close()
