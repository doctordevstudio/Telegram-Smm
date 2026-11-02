from __future__ import annotations

import logging
import time

from ..config import cfg
from ..utils import esc, parse_tg_link
from . import store

log = logging.getLogger("forcejoin")
_ok: dict[int, float] = {}  # uid -> cache expiry (positive results only)
OK_TTL = 300


async def get_force_channel() -> dict | None:
    return (await store.get_settings()).get("force_channel") or None


def join_text(fc: dict) -> str:
    return (f"📢 <b>Join our channel to continue</b>\n\n"
            f"Please join <b>{esc(fc.get('title', 'our channel'))}</b> and then tap "
            f"<b>✅ I've Joined</b>.")


async def is_joined(bot, uid: int) -> bool:
    fc = await get_force_channel()
    if not fc or uid in cfg.admin_ids:
        return True
    if _ok.get(uid, 0) > time.time():
        return True
    try:
        m = await bot.get_chat_member(fc["chat_id"], uid)
    except Exception as e:  # noqa: BLE001  fail-open so a bad config never bricks the bot
        log.error("force-join check failed (is the bot admin there?): %s", e)
        return True
    status = str(getattr(m.status, "value", m.status))
    ok = status in ("creator", "administrator", "member") or (status == "restricted" and getattr(m, "is_member", False))
    if ok:
        _ok[uid] = time.time() + OK_TTL
    return ok


async def set_force_channel(bot, ref: str | None) -> tuple[bool, str]:
    """Validate + save (or remove) the force-join channel. Reused by the admin panel later."""
    ref = (ref or "").strip()
    if ref.lower() in ("", "off", "none", "remove", "disable"):
        await store.set_setting("force_channel", None)
        _ok.clear()
        return True, "Force-join disabled."
    if ref.lstrip("-").isdigit():
        chat_ref: int | str = int(ref)
    else:
        link = parse_tg_link(ref)
        if not link or link.kind != "public":
            return False, "Send a public @username / link or the numeric chat ID (-100…)."
        chat_ref = f"@{link.username}"
    try:
        chat = await bot.get_chat(chat_ref)
        me = await bot.me()
        member = await bot.get_chat_member(chat.id, me.id)
    except Exception as e:  # noqa: BLE001
        return False, f"Can't access that chat: {e}"
    if str(getattr(member.status, "value", member.status)) not in ("administrator", "creator"):
        return False, "The bot must be an <b>admin</b> of that channel so it can check members."
    link_url = f"https://t.me/{chat.username}" if chat.username else (chat.invite_link or "")
    if not link_url:
        try:
            link_url = await bot.export_chat_invite_link(chat.id)
        except Exception:  # noqa: BLE001
            return False, "Private channel: give the bot the 'Invite users' right (or set a public username)."
    await store.set_setting("force_channel", {"chat_id": chat.id, "title": chat.title or "Channel", "link": link_url})
    _ok.clear()
    return True, f"✅ Force-join set to <b>{esc(chat.title)}</b>"
