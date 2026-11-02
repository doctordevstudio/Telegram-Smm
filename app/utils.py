"""Pure helpers (no aiogram / firebase imports) so they are easy to test."""
from __future__ import annotations

import asyncio
import html
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Coroutine
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from .constants import MAX_SPLIT_MINUTES, REACTION_EMOJIS, SHOW_VS16

log = logging.getLogger("utils")


def esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def now() -> int:
    return int(time.time())


def money(x: Any) -> str:
    x = round(float(x or 0), 2)
    return f"₹{int(x):,}" if x == int(x) else f"₹{x:,.2f}"


def fmt_ts(ts: Any, tz: str) -> str:
    return datetime.fromtimestamp(int(ts), ZoneInfo(tz)).strftime("%d %b %Y, %I:%M %p")


def day_key(tz: str, ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), ZoneInfo(tz)).strftime("%Y-%m-%d")


def calc_price(qty: int, unit: float) -> float:
    """qty * unit price, rounded UP to 2 decimals."""
    return math.ceil(round(qty * unit * 100, 6)) / 100


def paginate(items: list, page: int, size: int = 10) -> tuple[list, int, int]:
    pages = max(1, math.ceil(len(items) / size))
    page = min(max(1, page), pages)
    return items[(page - 1) * size: page * size], page, pages


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
_USER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_HASH_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_RESERVED = {
    "c", "s", "joinchat", "addlist", "addstickers", "addemoji", "share", "proxy", "socks",
    "setlanguage", "login", "contact", "boost", "iv", "m", "giftcode", "invoice", "bg",
    "addtheme", "confirmphone", "msg",
}


@dataclass
class TgLink:
    kind: str  # "public" | "invite" | "private_post"
    username: str | None = None
    invite_hash: str | None = None
    chat_id: int | None = None
    message_id: int | None = None
    stream: str | None = None  # value of ?livestream= / ?videochat= ("" when empty)

    @property
    def url(self) -> str:
        if self.kind == "invite":
            return f"https://t.me/+{self.invite_hash}"
        if self.kind == "public":
            return f"https://t.me/{self.username}"
        return ""


def parse_tg_link(text: str | None) -> TgLink | None:
    parts = (text or "").strip().split()
    if not parts:
        return None
    t = parts[0]
    if t.startswith("@"):
        return TgLink("public", username=t[1:]) if _USER_RE.match(t[1:]) else None
    if not re.match(r"^(https?://)?(www\.)?(t|telegram)\.me/", t, re.I):
        return TgLink("public", username=t) if _USER_RE.match(t) else None
    if not t.lower().startswith("http"):
        t = "https://" + t
    u = urlparse(t)
    segs = [p for p in u.path.split("/") if p]
    if not segs:
        return None
    q = parse_qs(u.query, keep_blank_values=True)
    stream = next((q[k][0] for k in ("livestream", "videochat", "voicechat") if k in q), None)
    first = segs[0]
    if first.startswith("+") and _HASH_RE.match(first[1:]):
        return TgLink("invite", invite_hash=first[1:], stream=stream)
    if first.lower() == "joinchat" and len(segs) > 1 and _HASH_RE.match(segs[1]):
        return TgLink("invite", invite_hash=segs[1], stream=stream)
    if first.lower() == "c" and len(segs) >= 2 and segs[1].isdigit():
        mid = next((int(p) for p in reversed(segs[2:]) if p.isdigit()), None)
        return TgLink("private_post", chat_id=int("-100" + segs[1]), message_id=mid, stream=stream)
    if first.lower() in _RESERVED or not _USER_RE.match(first):
        return None
    mid = next((int(p) for p in reversed(segs[1:]) if p.isdigit()), None)
    return TgLink("public", username=first, message_id=mid, stream=stream)


# ---------- view splits ----------
def fmt_minutes(m: int) -> str:
    if m <= 0:
        return "ASAP"
    h, r = divmod(int(m), 60)
    if h and r:
        return f"{h}h {r}m"
    return f"{h}h" if h else f"{r} min"


def fmt_splits(splits: list) -> str:
    if len(splits) == 1 and int(splits[0][1]) == 0:
        return "⚡ Fastest (safe speed)"
    return " ➜ ".join(f"{int(v):,} in {fmt_minutes(int(m))}" for v, m in splits)


def parse_splits(text: str, total: int, max_minutes: int = MAX_SPLIT_MINUTES):
    """'200 in 30' per line/comma. Returns (splits, error_html)."""
    chunks = [c for c in re.split(r"[\n,;]+", text or "") if c.strip()]
    if not chunks:
        return None, "Send at least one line like <code>200 in 30</code>"
    splits, tot_v, tot_m = [], 0, 0
    for c in chunks:
        nums = re.findall(r"\d+", c)
        if len(nums) != 2:
            return None, f"Couldn't read “{esc(c.strip())}”. Use the format <code>200 in 30</code>"
        v, m = int(nums[0]), int(nums[1])
        if v < 1:
            return None, "Each split needs at least 1 view."
        if m < 1 or m > max_minutes:
            return None, f"Minutes must be between 1 and {max_minutes}."
        splits.append([v, m])
        tot_v += v
        tot_m += m
    if tot_m > max_minutes:
        return None, f"Total time is {tot_m} min — the limit is {max_minutes} min (24h)."
    if tot_v != total:
        diff = total - tot_v
        word = f"{diff:,} more" if diff > 0 else f"{-diff:,} fewer"
        return None, f"Your splits add up to {tot_v:,} views, but your plan is {total:,}. Please use {word}."
    return splits, None


# ---------- reactions ----------
def norm_emoji(e: str) -> str:
    return e.replace("\ufe0f", "")


def show_emoji(e: str) -> str:
    return e + "\ufe0f" if e in SHOW_VS16 else e


def fmt_reactions(items: list) -> str:
    return "  ".join(f"{show_emoji(r['e'])} {int(r['n'])}" for r in items)


def parse_reactions(text: str, total: int, allowed: set[str] | list[str] | None = None):
    """'❤️15 😍20' -> ([{'e','n'}], error_html)."""
    cleaned = re.sub(r"[,;|]+", " ", text or "")
    pairs = re.findall(r"([^\d\s]+)\s*(\d+)", cleaned)
    if not pairs:
        return None, "Send reactions like <code>❤️15 😍20</code>"
    merged: dict[str, int] = {}
    for emo, n in pairs:
        key = norm_emoji(emo)
        if key not in REACTION_EMOJIS:
            return None, f"{esc(emo)} isn't a supported Telegram reaction."
        if allowed is not None and key not in set(allowed):
            return None, f"{esc(emo)} is disabled in your channel's reaction settings."
        if int(n) < 1:
            return None, "Each reaction needs a count of at least 1."
        merged[key] = merged.get(key, 0) + int(n)
    s = sum(merged.values())
    if s != total:
        diff = total - s
        word = f"{diff:,} more" if diff > 0 else f"{-diff:,} fewer"
        return None, f"That adds up to {s:,} reactions, but your plan is {total:,}. Please add {word}."
    return [{"e": k, "n": v} for k, v in merged.items()], None
