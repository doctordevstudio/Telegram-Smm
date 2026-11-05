"""The actual Telegram actions (Telethon). Every function receives an already-connected client."""
from __future__ import annotations

import asyncio
import random

from telethon import utils
from telethon.tl.functions.account import UpdateNotifySettingsRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import (CheckChatInviteRequest, GetMessagesViewsRequest, ImportChatInviteRequest,
                                            SendReactionRequest, SendVoteRequest)
from telethon.tl.types import ChatInviteAlready, InputNotifyPeer, InputPeerNotifySettings, ReactionEmoji

from ..utils import show_emoji
from .errors import TargetError


async def resolve(c, t: dict, need_join: bool):
    """-> (input_peer, joined_now, peer_id). Joins the channel when `need_join`."""
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
        raise TargetError("Invite link and channel belong to different chats")


async def subscribe(c, t: dict) -> dict:
    peer, joined, pid = await resolve(c, t, need_join=True)
    _check_id(t, pid)
    await asyncio.sleep(random.uniform(1, 3))
    return {"joined": joined, "pid": pid}


async def view(c, t: dict, message_id: int, need_join: bool) -> dict:
    peer, joined, pid = await resolve(c, t, need_join)
    _check_id(t, pid)
    if not await c.get_messages(peer, ids=message_id):
        raise TargetError("Post not found (deleted)")
    await asyncio.sleep(random.uniform(1, 3))
    await c(GetMessagesViewsRequest(peer=peer, id=[message_id], increment=True))
    return {"joined": joined, "pid": pid}


async def react(c, t: dict, message_id: int, emoji: str, need_join: bool) -> dict:
    peer, joined, pid = await resolve(c, t, need_join)
    _check_id(t, pid)
    if not await c.get_messages(peer, ids=message_id):
        raise TargetError("Post not found (deleted)")
    await asyncio.sleep(random.uniform(1, 3))
    for e in dict.fromkeys([emoji, show_emoji(emoji)]):
        try:
            await c(SendReactionRequest(peer=peer, msg_id=message_id, reaction=[ReactionEmoji(emoticon=e)]))
            return {"joined": joined, "pid": pid}
        except Exception as ex:  # noqa: BLE001
            if type(ex).__name__ != "ReactionInvalidError":
                raise
    raise TargetError("This reaction isn't allowed in that chat")


async def vote_poll(c, t: dict, message_id: int, need_join: bool) -> dict:
    peer, joined, pid = await resolve(c, t, need_join)
    _check_id(t, pid)
    msgs = await c.get_messages(peer, ids=message_id)
    if not msgs or not getattr(msgs, "poll", None):
        raise TargetError("Poll not found (deleted or already closed)")
    options = [a.option for a in msgs.poll.poll.answers if not msgs.poll.poll.quiz]
    if not options and msgs.poll.poll.quiz:  # quiz polls: pick the first option (can't know the correct one)
        options = [msgs.poll.poll.answers[0].option]
    if not options:
        raise TargetError("Poll has no options")
    await asyncio.sleep(random.uniform(1, 3))
    await c(SendVoteRequest(peer=peer, msg_id=message_id, options=[random.choice(options)]))
    return {"joined": joined, "pid": pid}


async def mute_toggle(c, t: dict, mute: bool, need_join: bool) -> dict:
    """Self-only action: the session toggles its own notification-mute state for the channel. Harmless
    (it only changes that account's own notification settings) - used purely as a configurable
    'daily actions' activity signal, as shown in the reference Mute Control panel."""
    peer, joined, pid = await resolve(c, t, need_join)
    _check_id(t, pid)
    await asyncio.sleep(random.uniform(1, 2))
    settings = InputPeerNotifySettings(mute_until=2**31 - 1 if mute else 0)
    await c(UpdateNotifySettingsRequest(peer=InputNotifyPeer(peer), settings=settings))
    return {"joined": joined, "pid": pid}
