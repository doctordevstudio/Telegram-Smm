"""The actual Telegram actions (Telethon). Every function receives an already-connected client."""
from __future__ import annotations

import asyncio
import random

from telethon import utils
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import (CheckChatInviteRequest, GetMessagesViewsRequest, ImportChatInviteRequest,
                                            SendReactionRequest)
from telethon.tl.types import ChatInviteAlready, ReactionEmoji

from ..utils import show_emoji
from .errors import TargetError


async def resolve(c, t: dict, need_join: bool):
    """-> (input_peer, joined_now, peer_id). Joins the channel when `need_join` (views/reactions/stream need membership)."""
    if t.get("invite_hash"):
        h = t["invite_hash"]
        res = await c(CheckChatInviteRequest(h))
        if isinstance(res, ChatInviteAlready):
            return await c.get_input_entity(res.chat), False, utils.get_peer_id(res.chat)
        upd = await c(ImportChatInviteRequest(h))
        chat = upd.chats[0]
        return await c.get_input_entity(chat), True, utils.get_peer_id(chat)
    ref = t.get("username") or t.get("chat_id")
    if not ref:
        raise TargetError("Channel can't be resolved")
    peer = await c.get_input_entity(ref)
    joined = False
    if need_join:
        await c(JoinChannelRequest(peer))
        joined = True
    return peer, joined, utils.get_peer_id(peer)


def _check_id(t: dict, pid: int) -> None:
    if t.get("chat_id") and t["chat_id"] != pid:
        raise TargetError("Invite link and post belong to different channels")


async def subscribe(c, t: dict) -> dict:
    peer, joined, pid = await resolve(c, t, need_join=True)
    _check_id(t, pid)
    await asyncio.sleep(random.uniform(1, 3))
    return {"joined": joined, "pid": pid}


async def view(c, t: dict, need_join: bool) -> dict:
    peer, joined, pid = await resolve(c, t, need_join)
    _check_id(t, pid)
    if not await c.get_messages(peer, ids=t["message_id"]):
        raise TargetError("Post not found (deleted or wrong link)")
    await asyncio.sleep(random.uniform(1, 4))
    await c(GetMessagesViewsRequest(peer=peer, id=[t["message_id"]], increment=True))
    return {"joined": joined, "pid": pid}


async def react(c, t: dict, emoji: str, need_join: bool) -> dict:
    peer, joined, pid = await resolve(c, t, need_join)
    _check_id(t, pid)
    if not await c.get_messages(peer, ids=t["message_id"]):
        raise TargetError("Post not found (deleted or wrong link)")
    await asyncio.sleep(random.uniform(1, 4))
    for e in dict.fromkeys([emoji, show_emoji(emoji)]):  # some emojis need the VS16 variant
        try:
            await c(SendReactionRequest(peer=peer, msg_id=t["message_id"], reaction=[ReactionEmoji(emoticon=e)]))
            return {"joined": joined, "pid": pid}
        except Exception as ex:  # noqa: BLE001
            if type(ex).__name__ != "ReactionInvalidError":
                raise
    raise TargetError("This reaction isn't allowed in that chat")
