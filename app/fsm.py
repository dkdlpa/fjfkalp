"""FSM-хранилище с TTL, чтобы состояния «зависших» пользователей не текли в RAM.

Штатный MemoryStorage никогда не удаляет записи: каждый, кто открыл форму
поиска и ушёл, навсегда оставлял после себя словарь состояния. Здесь время
последнего обращения к ключу отслеживается, а фоновая задача периодически
вызывает prune().
"""
from __future__ import annotations

import os
import time

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import StateType, StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

# Состояния «зависших» пользователей живут ограниченно. По умолчанию 6 часов
# вместо суток: меньше RAM, при этом форма поиска не «сгорает» за сессию.
DEFAULT_STATE_TTL = int(os.getenv("FSM_STATE_TTL", "21600"))


class TTLMemoryStorage(MemoryStorage):
    def __init__(self) -> None:
        super().__init__()
        self._touched: dict[StorageKey, float] = {}

    def _touch(self, key: StorageKey) -> None:
        self._touched[key] = time.monotonic()

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        self._touch(key)
        self.storage[key].state = state.state if isinstance(state, State) else state

    async def get_state(self, key: StorageKey) -> str | None:
        self._touch(key)
        return self.storage[key].state

    async def set_data(self, key: StorageKey, data: dict) -> None:
        self._touch(key)
        self.storage[key].data = data.copy()

    async def get_data(self, key: StorageKey) -> dict:
        self._touch(key)
        return self.storage[key].data.copy()

    async def get_value(self, storage_key: StorageKey, dict_key: str, default=None):
        self._touch(storage_key)
        return await super().get_value(storage_key, dict_key, default)

    async def prune(self, ttl: float = DEFAULT_STATE_TTL) -> None:
        cutoff = time.monotonic() - ttl
        stale = [key for key, ts in self._touched.items() if ts < cutoff]
        for key in stale:
            self._touched.pop(key, None)
            self.storage.pop(key, None)

    async def close(self) -> None:
        self._touched.clear()
        self.storage.clear()