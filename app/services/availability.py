"""How many sessions can still be used for a target (counts the `targets/*` member sets)."""
from __future__ import annotations

import asyncio

from .. import db
from . import store

FIELD = {"subscribers": "subs", "views": "views", "reactions": "reacts", "stream": "live"}


async def sessions_total() -> int:
    return int(await db.cached_get("stats/sessions_active", ttl=10) or 0)


def channel_key(t: dict) -> str:
    if t.get("invite_hash"):  # private channels are always keyed by invite hash (stable before/after join)
        return f"h{t['invite_hash']}"
    if t.get("chat_id"):
        return f"c{t['chat_id']}"
    return f"u{(t.get('username') or '').lower()}"


def target_key(svc: str, t: dict) -> str:
    base = channel_key(t)
    if svc == "subscribers":
        return base
    if svc in ("views", "reactions"):
        return f"{base}_m{t['message_id']}"
    return f"{base}_s"


async def get_available(svc: str, t: dict) -> tuple[int, int]:
    """(available, total_active_sessions)"""
    total = await sessions_total()
    if svc == "stream":
        # an account can sit in only ONE call at a time -> exclusivity is global, plus the RAM cap
        live = len(await db.keys("live"))
        cap = int((await store.get_safety())["stream_cap"])
        return max(0, min(total - live, cap - live)), total
    used = len(await db.keys(f"targets/{target_key(svc, t)}/{FIELD[svc]}"))
    return max(0, total - used), total
