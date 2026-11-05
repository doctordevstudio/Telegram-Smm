"""Resolve + validate channels/groups the user sends."""
from __future__ import annotations

import logging

from aiogram.exceptions import TelegramAPIError

from ..utils import TgLink, norm_emoji, now

log = logging.getLogger("resolver")

ERR_NOT_FOUND = ("❌ <b>Channel not found.</b>\n\nMake sure the link is correct and the channel is "
                 "<b>public</b> (or send a private invite link like <code>t.me/+AbCd…</code>).")


async def get_chat(bot, ref):
    try:
        return await bot.get_chat(ref)
    except TelegramAPIError as e:
        log.info("get_chat(%s) failed: %s", ref, e)
        return None


async def chat_record(bot, chat, link_url: str) -> dict:
    try:
        members = await bot.get_chat_member_count(chat.id)
    except TelegramAPIError:
        members = None
    ar = getattr(chat, "available_reactions", None)
    ctype = getattr(chat.type, "value", chat.type)
    rec = {"type": "public", "chat_id": chat.id, "username": chat.username, "title": chat.title or chat.username,
           "link": link_url, "members": members, "kind": str(ctype), "verified": True, "added_at": now()}
    if ar:  # None => all emoji allowed
        rec["reactions_allowed"] = [norm_emoji(r.emoji) for r in ar if getattr(r, "emoji", None)]
    return rec


async def check_invite(bot, invite_hash: str):
    """Bot API cannot look inside private invite links.
    PHASE 2 HOOK: use a healthy Telethon session -> CheckChatInviteRequest to confirm the link is
    valid/not revoked. Until then private links are only format-validated."""
    return None


async def resolve_channel(bot, link: TgLink) -> dict | str:
    """Returns a channel record, or an error message (HTML) for the user."""
    if link.kind == "public":
        chat = await get_chat(bot, f"@{link.username}")
        if chat is None:
            return ERR_NOT_FOUND
        if str(getattr(chat.type, "value", chat.type)) not in ("channel", "supergroup", "group"):
            return "❌ That username belongs to a <b>user/bot</b>, not a channel or group."
        return await chat_record(bot, chat, link.url)
    if link.kind == "invite":
        await check_invite(bot, link.invite_hash)
        return {"type": "private", "title": "Private Channel", "link": link.url, "invite_hash": link.invite_hash,
                "chat_id": None, "username": None, "members": None, "verified": False, "added_at": now()}
    return ("🔒 That's a <b>post link</b> from a private channel.\n\nPlease send the channel's "
            "<b>invite link</b> instead (looks like <code>https://t.me/+AbCdEf…</code>).")
