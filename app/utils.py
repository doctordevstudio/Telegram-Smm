"""Pure helpers (no aiogram / firebase imports) so they are easy to test."""
from __future__ import annotations

import asyncio
import html
import logging
import math
import random
import time
from datetime import datetime
from typing import Any, Coroutine
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from .constants import SHOW_VS16

log = logging.getLogger("utils")


def esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def now() -> int:
    return int(time.time())


def fmt_ts(ts: Any, tz: str) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(int(ts), ZoneInfo(tz)).strftime("%d %b %Y, %I:%M %p")


def day_key(tz: str, ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), ZoneInfo(tz)).strftime("%Y-%m-%d")


def paginate(items: list, page: int, size: int = 10) -> tuple[list, int, int]:
    pages = max(1, math.ceil(len(items) / size))
    page = min(max(1, page), pages)
    return items[(page - 1) * size: page * size], page, pages


def fmt_seconds(s: float) -> str:
    s = float(s)
    if s < 1:
        return f"{s:g}s"
    if s < 60:
        return f"{s:g}s"
    m, r = divmod(int(s), 60)
    if m < 60:
        return f"{m}m {r}s" if r else f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h {m}m" if m else f"{h}h"


def fmt_days_left(expires_at: int | None) -> str:
    if not expires_at:
        return "no expiry"
    left = expires_at - now()
    if left <= 0:
        return "expired"
    d = left / 86400
    return f"{d:.1f} days left" if d < 2 else f"{int(d)} days left"


# ---------- background tasks ----------
_bg: set[asyncio.Task] = set()


def _done(t: asyncio.Task) -> None:
    _bg.discard(t)
    if not t.cancelled() and t.exception():
        log.error("background task failed", exc_info=t.exception())


def spawn(coro: Coroutine) -> asyncio.Task:
    t = asyncio.create_task(coro)
    _bg.add(t)
    t.add_done_callback(_done)
    return t


# ---------- telegram links ----------
class TgLink:
    __slots__ = ("kind", "username", "invite_hash")

    def __init__(self, kind: str, username: str | None = None, invite_hash: str | None = None):
        self.kind, self.username, self.invite_hash = kind, username, invite_hash

    @property
    def url(self) -> str:
        return f"https://t.me/+{self.invite_hash}" if self.kind == "invite" else f"https://t.me/{self.username}"


_USER_RE = __import__("re").compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_HASH_RE = __import__("re").compile(r"^[A-Za-z0-9_-]{8,64}$")
_RESERVED = {"c", "s", "joinchat", "addlist", "addstickers", "addemoji", "share", "proxy", "socks", "login",
            "contact", "boost", "iv", "m", "giftcode", "invoice", "bg", "addtheme", "confirmphone", "msg"}


def parse_tg_link(text: str | None) -> TgLink | None:
    t = (text or "").strip().split()
    if not t:
        return None
    t = t[0]
    if t.startswith("@"):
        return TgLink("public", username=t[1:]) if _USER_RE.match(t[1:]) else None
    if not __import__("re").match(r"^(https?://)?(www\.)?(t|telegram)\.me/", t, __import__("re").I):
        return TgLink("public", username=t) if _USER_RE.match(t) else None
    if not t.lower().startswith("http"):
        t = "https://" + t
    u = urlparse(t)
    segs = [p for p in u.path.split("/") if p]
    if not segs:
        return None
    first = segs[0]
    if first.startswith("+") and _HASH_RE.match(first[1:]):
        return TgLink("invite", invite_hash=first[1:])
    if first.lower() == "joinchat" and len(segs) > 1 and _HASH_RE.match(segs[1]):
        return TgLink("invite", invite_hash=segs[1])
    if first.lower() in _RESERVED or not _USER_RE.match(first):
        return None
    return TgLink("public", username=first)


# ---------- reactions ----------
def norm_emoji(e: str) -> str:
    return e.replace("\ufe0f", "")


def show_emoji(e: str) -> str:
    return e + "\ufe0f" if e in SHOW_VS16 else e


def pick_weighted_emoji(mix: dict[str, float], pool: list[str]) -> str:
    """mix = {emoji: percent}. Unassigned pool emojis share the remaining probability equally
    ('System Random'). If mix sums over 100, it's normalised proportionally first."""
    pool = pool or list(mix) or ["👍"]
    weights = {e: max(0.0, float(mix.get(e, 0))) for e in set(pool) | set(mix)}
    total = sum(weights.values())
    if total > 100:
        weights = {e: w * 100 / total for e, w in weights.items()}
        total = 100.0
    remaining = max(0.0, 100.0 - total)
    unassigned = [e for e in pool if mix.get(e, 0) <= 0]
    if remaining > 0 and unassigned:
        share = remaining / len(unassigned)
        for e in unassigned:
            weights[e] = weights.get(e, 0) + share
    elif not any(weights.values()):
        weights = {e: 1.0 for e in pool}
    items = [(e, w) for e, w in weights.items() if w > 0]
    if not items:
        return random.choice(pool)
    return random.choices([e for e, _ in items], weights=[w for _, w in items], k=1)[0]


# ---------- misc ----------
def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
