"""Map Telethon exceptions (by class name, so it survives library renames) to engine decisions."""
from __future__ import annotations

import asyncio


class TargetError(Exception):
    """The order's target itself is bad (post missing, invite expired...). Retrying with other sessions is pointless."""


class StreamNotLive(Exception):
    pass


_FLOOD = {"FloodWaitError", "SlowModeWaitError", "FloodPremiumWaitError"}
_DEAD = {"AuthKeyUnregisteredError", "AuthKeyInvalidError", "AuthKeyDuplicatedError", "UserDeactivatedError",
         "UserDeactivatedBanError", "SessionRevokedError", "SessionExpiredError", "PhoneNumberBannedError"}
_LIMITED = {"PeerFloodError"}
_FULL = {"ChannelsTooMuchError", "UserChannelsTooMuchError"}
_TARGET = {"InviteHashExpiredError", "InviteHashInvalidError", "InviteHashEmptyError", "ChannelPrivateError",
           "ChannelInvalidError", "UsernameNotOccupiedError", "UsernameInvalidError", "InviteRequestSentError",
           "MessageIdInvalidError", "GroupcallInvalidError", "GroupcallForbiddenError", "TargetError", "StreamNotLive"}
_BANNED_HERE = {"UserBannedInChannelError", "ChannelBannedError", "UserKickedError"}
_ALREADY = {"UserAlreadyParticipantError"}


def classify(e: BaseException) -> tuple[str, int, str]:
    """-> (kind, value, note). kinds: flood|dead|limited|full|target|banned_here|already|retry"""
    n = type(e).__name__
    if n in _FLOOD:
        return "flood", int(getattr(e, "seconds", 60) or 60), f"FloodWait {getattr(e, 'seconds', '?')}s"
    if n in _DEAD:
        return "dead", 0, n.replace("Error", "")
    if n in _LIMITED:
        return "limited", 24 * 3600, "PeerFlood (account limited by Telegram)"
    if n in _FULL:
        return "full", 0, "Joined too many channels"
    if n in _ALREADY:
        return "already", 0, ""
    if n in _BANNED_HERE:
        return "banned_here", 0, "Banned in this channel"
    if n in _TARGET:
        return "target", 0, str(e) if n in ("TargetError", "StreamNotLive") else _nice(n)
    if isinstance(e, (asyncio.TimeoutError, ConnectionError, OSError)):
        return "retry", 0, "network"
    return "retry", 0, f"{n}: {str(e)[:120]}"


def _nice(n: str) -> str:
    return {"InviteHashExpiredError": "Invite link expired or revoked", "InviteHashInvalidError": "Invalid invite link",
            "ChannelPrivateError": "Channel is private / unreachable", "UsernameNotOccupiedError": "Channel username not found",
            "InviteRequestSentError": "Channel requires admin approval to join (join-request link)",
            "MessageIdInvalidError": "Post not found", "GroupcallInvalidError": "Live stream not found / ended",
            "GroupcallForbiddenError": "Live stream is not accepting viewers"}.get(n, n.replace("Error", ""))
