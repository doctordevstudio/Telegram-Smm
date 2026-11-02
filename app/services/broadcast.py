"""Broadcast engine shared by the /broadcast bot command and the admin panel.

Content lives in Telegram: we only keep (from_chat, message_id) of the source message, plus the id of
every delivered copy (broadcast_msgs/{id}/{uid}) so a broadcast can later be edited or deleted for everyone.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from .. import db
from ..config import cfg
from ..utils import day_key, now, spawn
from . import notify, store

log = logging.getLogger("broadcast")
_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def describe(msg) -> tuple[str, str]:
    """(kind, preview) of a telegram Message."""
    kind = "text" if msg.text else "photo" if msg.photo else "video" if msg.video else \
        "document" if msg.document else "animation" if msg.animation else "other"
    return kind, (msg.text or msg.caption or f"[{kind}]")[:200]


async def create(bot, admin_id: int, from_chat: int, msg_id: int, kind: str, preview: str, origin: str | None = None) -> str:
    bid = f"{await db.next_id('broadcasts'):09d}"
    rec = {"id": bid, "from_chat": from_chat, "message_id": msg_id, "kind": kind, "preview": preview, "created_at": now(),
           "by": admin_id, "status": "running", "sent": 0, "blocked": 0, "failed": 0, "total": 0}
    if origin:
        rec["origin"] = origin
    await db.multi_update({f"broadcasts/{bid}": rec, f"idx/broadcasts_by_day/{day_key(cfg.timezone)}/{bid}": True})
    _spawn(_run(bot, admin_id, bid, from_chat, msg_id))
    return bid


async def _run(bot, admin_id: int, bid: str, from_chat: int, msg_id: int) -> None:
    uids = [int(u) for u in await db.keys("users")]
    uids = [u for u in uids if u not in store.BANNED]
    stat = {"sent": 0, "blocked": 0, "failed": 0}
    sent_map: dict[str, int] = {}

    async def one(uid: int) -> None:
        for _ in range(2):
            try:
                m = await bot.copy_message(uid, from_chat, msg_id)
                stat["sent"] += 1
                sent_map[f"broadcast_msgs/{bid}/{uid}"] = m.message_id
                return
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramForbiddenError:
                stat["blocked"] += 1
                return
            except Exception:  # noqa: BLE001
                stat["failed"] += 1
                return
        stat["failed"] += 1

    for i in range(0, len(uids), 25):  # ~25 msgs/sec, under Telegram's 30/sec limit
        await asyncio.gather(*(one(u) for u in uids[i:i + 25]))
        if sent_map:
            await db.multi_update(sent_map)
            sent_map.clear()
        if (i // 25) % 8 == 0:
            await db.update(f"broadcasts/{bid}", {**stat, "total": len(uids)})
        await asyncio.sleep(1)
    await db.update(f"broadcasts/{bid}", {"status": "done", "total": len(uids), "finished_at": now(), **stat})
    store.log_bg("admin", admin_id, "broadcast_done", f"#{bid} {stat}")
    await notify.notify_user(bot, admin_id, (
        f"✅ <b>Broadcast #{int(bid)} finished</b>\n\n👥 Total: {len(uids):,}\n✅ Delivered: {stat['sent']:,}\n"
        f"🚫 Blocked bot: {stat['blocked']:,}\n❌ Failed: {stat['failed']:,}"))


async def rebroadcast(bot, admin_id: int, bid: str) -> str | None:
    b = await db.get(f"broadcasts/{bid}")
    if not b or b.get("status") == "deleted":
        return None
    return await create(bot, admin_id, b["from_chat"], b["message_id"], b["kind"], b["preview"], origin=bid)


async def _for_each_delivered(bot, bid: str, fn) -> tuple[int, int]:
    m = await db.get(f"broadcast_msgs/{bid}", {})
    items, ok, bad = list(m.items()), 0, 0
    for i in range(0, len(items), 25):
        res = await asyncio.gather(*(fn(int(u), int(mid)) for u, mid in items[i:i + 25]), return_exceptions=True)
        ok += sum(1 for r in res if r is True)
        bad += sum(1 for r in res if r is not True)
        await asyncio.sleep(1)
    return ok, bad


async def edit(bot, bid: str, text: str) -> bool:
    """Edit text/caption of the source message AND every delivered copy."""
    b = await db.get(f"broadcasts/{bid}")
    if not b or b["kind"] not in ("text", "photo", "video", "document", "animation"):
        return False

    async def do(chat: int, mid: int) -> bool:
        if b["kind"] == "text":
            await bot.edit_message_text(text, chat_id=chat, message_id=mid)
        else:
            await bot.edit_message_caption(chat_id=chat, message_id=mid, caption=text)
        return True

    try:
        await do(b["from_chat"], b["message_id"])
    except Exception as e:  # noqa: BLE001
        if "not modified" not in str(e):
            return False

    async def bg():
        ok, bad = await _for_each_delivered(bot, bid, do)
        await db.update(f"broadcasts/{bid}", {"preview": text[:200], "edited_at": now(), "edit_ok": ok, "edit_failed": bad})

    _spawn(bg())
    return True


async def delete(bot, bid: str) -> bool:
    b = await db.get(f"broadcasts/{bid}")
    if not b:
        return False

    async def do(chat: int, mid: int) -> bool:
        await bot.delete_message(chat, mid)
        return True

    async def bg():
        await _for_each_delivered(bot, bid, do)
        try:
            await bot.delete_message(b["from_chat"], b["message_id"])
        except Exception:  # noqa: BLE001
            pass
        await db.multi_update({f"broadcast_msgs/{bid}": None})
        await db.update(f"broadcasts/{bid}", {"status": "deleted", "deleted_at": now()})

    await db.update(f"broadcasts/{bid}", {"status": "deleting"})
    _spawn(bg())
    return True
