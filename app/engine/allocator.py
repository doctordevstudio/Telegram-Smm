"""Picks sessions for actions. One global lock => two orders can never receive the same session."""
from __future__ import annotations

import asyncio
import random

from ..utils import now
from .sessions import SessionStore


class Allocator:
    def __init__(self, sessions: SessionStore) -> None:
        self.s = sessions
        self.lock = asyncio.Lock()

    def _ok(self, sid: str, m: dict, t: int, sf: dict, exclude: set, need_join: bool) -> bool:
        if m.get("status") != "active" or m.get("flood_until", 0) > t or sid in exclude or sid in self.s.busy:
            return False
        u = self.s.usage(sid)
        if t - u["last"] < sf["cooldown_sec"]:
            return False
        if sum(1 for x in u["a"] if t - x < 3600) >= sf["actions_per_hour"]:
            return False
        if need_join and (m.get("full") or sum(1 for x in u["j"] if t - x < 86400) >= sf["joins_per_day"]):
            return False
        return True

    async def pick(self, n: int, *, sf: dict, exclude: set | None = None, members: set | None = None,
                   always_join: bool = False) -> list[str]:
        """members = sessions already inside the channel (they don't consume a 'join' from the daily cap)."""
        exclude, members, t = exclude or set(), members or set(), now()
        async with self.lock:
            cand = [sid for sid, m in self.s.meta.items()
                    if self._ok(sid, m, t, sf, exclude, always_join or sid not in members)]
            random.shuffle(cand)
            cand.sort(key=lambda s: len([x for x in self.s.usage(s)["a"] if t - x < 3600]))  # least-used first
            chosen = cand[:n]
            self.s.busy.update(chosen)
            return chosen

    def release(self, sid: str) -> None:
        self.s.busy.discard(sid)
