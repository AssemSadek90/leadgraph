"""Disk cache for every HTTP fetch, Places request and LLM call (outputs/.cache).

Keys are a hash of the request parts. API keys are never part of a key or a value.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import diskcache

_MISSING = object()


def request_hash(*parts: Any) -> str:
    blob = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class Cache:
    def __init__(self, directory: Path | str, enabled: bool = True):
        self.directory = Path(directory)
        self.enabled = enabled
        self._cache = diskcache.Cache(str(self.directory)) if enabled else None
        self.hits: Counter[str] = Counter()
        self.misses: Counter[str] = Counter()

    def get(self, namespace: str, *parts: Any) -> Any | None:
        if self._cache is None:
            self.misses[namespace] += 1
            return None
        value = self._cache.get(f"{namespace}:{request_hash(*parts)}", default=_MISSING)
        if value is _MISSING:
            self.misses[namespace] += 1
            return None
        self.hits[namespace] += 1
        return value

    def set(self, namespace: str, *parts: Any, value: Any, expire: float | None = None) -> None:
        if self._cache is not None:
            self._cache.set(f"{namespace}:{request_hash(*parts)}", value, expire=expire)

    def stats(self) -> dict[str, dict[str, int]]:
        names = sorted(set(self.hits) | set(self.misses))
        return {n: {"hits": self.hits[n], "misses": self.misses[n]} for n in names}

    def close(self) -> None:
        if self._cache is not None:
            self._cache.close()
