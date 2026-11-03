"""High-performance in-memory TTL Cache.

Keeps hot search results and TMDB metadata in RAM to support 50k+ daily users
with minimal latency and zero database choke.
"""

from __future__ import annotations

import time
from typing import Any


class TTLCache:
    def __init__(self, default_ttl_seconds: int = 300, max_size: int = 2000):
        self._ttl = default_ttl_seconds
        self._max_size = max_size
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        if key not in self._data:
            return None
        expires_at, val = self._data[key]
        if time.time() > expires_at:
            del self._data[key]
            return None
        return val

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        if len(self._data) >= self._max_size:
            # Evict expired or oldest entries
            now = time.time()
            expired_keys = [k for k, (exp, _) in self._data.items() if now > exp]
            if expired_keys:
                for k in expired_keys[:200]:
                    self._data.pop(k, None)
            else:
                # Remove first 100 items if nothing expired
                keys_to_pop = list(self._data.keys())[:100]
                for k in keys_to_pop:
                    self._data.pop(k, None)

        ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        self._data[key] = (time.time() + ttl, value)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()


# Global cache instances
SEARCH_CACHE = TTLCache(default_ttl_seconds=300, max_size=3000)
TMDB_CACHE = TTLCache(default_ttl_seconds=1800, max_size=1000)

