"""Thin async wrapper around firebase-admin (Realtime Database).

firebase-admin is synchronous, so every call runs in its own thread pool and
never blocks the event loop (bot stays responsive for many users at once).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import firebase_admin
from firebase_admin import credentials
from firebase_admin import db as rtdb

log = logging.getLogger("db")
_pool = ThreadPoolExecutor(max_workers=24, thread_name_prefix="fb")
_cache: dict[str, tuple[float, Any]] = {}


def init(cfg) -> None:
    if firebase_admin._apps:
        return
    if cfg.firebase_cred_json:
        cred = credentials.Certificate(json.loads(cfg.firebase_cred_json))
    else:
        cred = credentials.Certificate(cfg.firebase_cred_file)
    firebase_admin.initialize_app(cred, {"databaseURL": cfg.firebase_db_url, "httpTimeout": 15})


async def _run(fn: Callable[[], Any]) -> Any:
    return await asyncio.get_running_loop().run_in_executor(_pool, fn)


def as_dict(v: Any) -> dict:
    """Realtime Database returns a *list* (with None holes) instead of an object when a node's keys look like a
    dense integer sequence (e.g. post ids 1,2,3...). Normalise to {str(key): value} so callers can use .items()."""
    if isinstance(v, dict):
        return v
    if isinstance(v, list):
        return {str(i): x for i, x in enumerate(v) if x is not None}
    return {}


def _ref(path: str):
    return rtdb.reference(path)


async def get(path: str, default: Any = None) -> Any:
    v = await _run(lambda: _ref(path).get())
    return default if v is None else v


async def keys(path: str) -> list[str]:
    """Child keys only (shallow read) - cheap even for huge nodes."""
    v = await _run(lambda: _ref(path).get(shallow=True))
    return list(v.keys()) if isinstance(v, dict) else []


async def put(path: str, value: Any) -> None:
    await _run(lambda: _ref(path).set(value))


async def update(path: str, data: dict) -> None:
    await _run(lambda: _ref(path).update(data))


async def multi_update(updates: dict) -> None:
    """Atomic multi-path write. Keys are full paths, value None deletes."""
    await _run(lambda: rtdb.reference("/").update(updates))


async def push(path: str, value: Any) -> str:
    ref = await _run(lambda: _ref(path).push(value))
    return ref.key


async def delete(path: str) -> None:
    await _run(lambda: _ref(path).delete())


async def txn(path: str, fn: Callable[[Any], Any]) -> Any:
    """Atomic read-modify-write. Raise inside fn to abort."""
    return await _run(lambda: _ref(path).transaction(fn))


async def incr(path: str, by: float = 1):
    def _f(cur):
        new = (cur or 0) + by
        return round(new, 2) if isinstance(new, float) else new

    return await txn(path, _f)


async def next_id(name: str) -> int:
    return int(await incr(f"counters/{name}"))


async def query_last(path: str, n: int) -> dict:
    """Last n children by key (push ids / zero-padded ids are chronological)."""
    v = await _run(lambda: _ref(path).order_by_key().limit_to_last(n).get())
    return v if isinstance(v, dict) else {}


async def cached_get(path: str, ttl: float = 20.0) -> Any:
    hit = _cache.get(path)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    v = await get(path)
    _cache[path] = (time.monotonic() + ttl, v)
    return v


def invalidate(*prefixes: str) -> None:
    if not prefixes:
        _cache.clear()
        return
    for k in list(_cache):
        if any(k.startswith(p) for p in prefixes):
            _cache.pop(k, None)
