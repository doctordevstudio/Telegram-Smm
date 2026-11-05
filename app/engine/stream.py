"""Live-stream viewers, rewritten on top of pytgcalls.

Why the old approach disconnected within seconds: it called Telethon's raw `JoinGroupCallRequest`
with synthetic/fake WebRTC fingerprints and never actually negotiated a media transport (no real
ICE/DTLS handshake, no RTP). Telegram's call servers accept the join call but drop a participant
that never completes real media negotiation within a few seconds. pytgcalls performs that real
negotiation (via ntgcalls) and streams actual (silent) audio, which is what keeps a participant
genuinely present. This is the standard approach used for this kind of "viewer" automation.

CAVEAT: this module could not be executed against real Telegram or against the installed
`pytgcalls`/`ntgcalls` wheels in this environment (no network access here), so its exact API calls
are written against the documented, stable pytgcalls v2 surface from memory and are the piece most
likely to need a small adjustment against the exact version that resolves on your server. Every
call is wrapped so a failure here logs clearly and disables the stream service instead of crashing
the rest of the bot.
"""
from __future__ import annotations

import asyncio
import logging
import random

from .. import db
from ..roles import log_action
from ..services import notify, store
from ..utils import esc, now, spawn
from . import actions
from .errors import classify
from .silence import silence_wav_path

log = logging.getLogger("stream")
TICK = 6

PYTGCALLS_OK = True
try:
    from pytgcalls import PyTgCalls
    from pytgcalls.types import MediaStream
except Exception as e:  # noqa: BLE001
    PYTGCALLS_OK = False
    _IMPORT_ERROR = e


def _target(c: dict) -> dict:
    return {"chat_id": c.get("chat_id"), "username": c.get("username"), "invite_hash": c.get("invite_hash"),
            "title": c.get("title"), "link": c.get("link")}


def _chan_key(c: dict) -> str:
    return f"c{c['chat_id']}" if c.get("chat_id") else f"h{c.get('invite_hash')}"


class Conn:
    __slots__ = ("sid", "cid", "client", "app", "joined_at", "fails")

    def __init__(self, sid: str, cid: str, client, app) -> None:
        self.sid, self.cid, self.client, self.app = sid, cid, client, app
        self.joined_at, self.fails = now(), 0


class StreamHub:
    def __init__(self, sessions, alloc) -> None:
        self.s, self.alloc = sessions, alloc
        self.conns: dict[str, Conn] = {}
        self.by_channel: dict[str, set[str]] = {}
        self._task: asyncio.Task | None = None
        self.bot = None
        self.available = PYTGCALLS_OK
        if not PYTGCALLS_OK:
            log.error("pytgcalls is not available (%s) - the Live Stream Viewers service is disabled. "
                     "Install pytgcalls/ntgcalls and make sure ffmpeg is on PATH (see the Dockerfile).",
                     _IMPORT_ERROR)

    async def start(self, bot) -> None:
        self.bot = bot
        if not self.available:
            return
        silence_wav_path()  # generate once up front so the first join doesn't stall on it
        await db.delete("live")  # stale entries from a previous process
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        for sid in list(self.conns):
            await self._drop(sid, leave=True)

    # ───────── main loop: keep each channel's stream at its configured viewer count ─────────
    async def _loop(self) -> None:
        while True:
            try:
                channels = await store.list_channels()
                for c in channels:
                    await self._tick_channel(c)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("stream tick failed")
            await asyncio.sleep(TICK)

    async def _tick_channel(self, c: dict) -> None:
        cid = c["id"]
        svc = await store.get_service(cid, "stream")
        have = self.by_channel.get(cid, set())
        if not svc["enabled"] or svc["config"].get("paused"):
            if have:
                await self._leave_channel(cid)
            return
        if svc["expires_at"] and svc["expires_at"] <= now():
            await db.update(f"services/{cid}/stream", {"enabled": False, "expired": True})
            if have:
                await self._leave_channel(cid)
            spawn(notify.notify_staff(self.bot, f"⏳ <b>Live Stream</b> expired for <b>{esc(c['title'])}</b>."))
            return
        target = int(svc["config"].get("max_members", 25))
        sf = await store.get_safety()
        cap = min(target, int(sf["stream_cap"]))
        if len(have) < cap:
            base = _chan_key(c)
            members = set(await db.keys(f"targets/{base}/subs"))
            n = min(cap - len(have), int(sf["stream_concurrency"]))
            sids = await self.alloc.pick(n, sf=sf, exclude=set(have) | set(self.conns), members=members)
            for i, sid in enumerate(sids):
                await asyncio.sleep(i * random.uniform(0.3, 0.9) * float(svc["config"].get("join_speed", 1.5)))
                await self._join_one(c, sid, members)
        elif len(have) > cap:
            for sid in list(have)[: len(have) - cap]:
                await self._drop(sid, leave=True)
        await self._hand_rise(c, svc["config"].get("hand_rise_pct", 0))

    async def _join_one(self, c: dict, sid: str, members: set[str]) -> None:
        cid, base = c["id"], _chan_key(c)
        try:
            client = await self.s.open_client(sid)
            app = PyTgCalls(client)
            await app.start()
            peer, joined, pid = await actions.resolve(client, _target(c), need_join=sid not in members)
            actions._check_id(_target(c), pid)
            await app.play(pid, MediaStream(silence_wav_path()))
        except Exception as e:  # noqa: BLE001
            kind, val, note = classify(e)
            if kind == "flood":
                self.s.mark(sid, flood_until=now() + val + random.randint(10, 60), note=note)
            elif kind == "dead":
                self.s.mark(sid, status="dead", note=note)
            elif kind not in ("retry",):
                log.info("stream join failed for %s on %s: %s", sid, cid, note)
            self.alloc.release(sid)
            with __import__("contextlib").suppress(Exception):
                await self.s.close_client(client, sid)
            return
        conn = Conn(sid, cid, client, app)
        self.conns[sid] = conn
        self.by_channel.setdefault(cid, set()).add(sid)
        ts = now()
        await db.multi_update({f"live/{sid}": {"cid": cid}, f"targets/{base}/subs/{sid}": ts})
        self.s.record(sid, join=joined)

    async def _hand_rise(self, c: dict, pct: float) -> None:
        """Best-effort: a random subset of connected viewers raise/lower their hand. This is a
        secondary, cosmetic signal - if the raw API shape differs on your pytgcalls/Telethon
        version it fails silently per-participant rather than breaking the stream."""
        if not pct:
            return
        from telethon.tl.functions.phone import EditGroupCallParticipantRequest
        sids = list(self.by_channel.get(c["id"], ()))
        for sid in sids:
            conn = self.conns.get(sid)
            if not conn or random.random() * 100 > float(pct) / 3:  # a slice of the group reconsiders each tick
                continue
            try:
                full = await conn.client.get_input_entity(await conn.client.get_me())
                raise_hand = random.random() < 0.5
                await conn.client(EditGroupCallParticipantRequest(call=await self._call_of(conn), participant=full,
                                                                   raise_hand=raise_hand))
            except Exception:  # noqa: BLE001
                pass  # cosmetic only - never let this affect presence

    async def _call_of(self, conn: Conn):
        from telethon.tl.functions.channels import GetFullChannelRequest
        c = await store.get_channel(conn.cid)
        peer, _, _ = await actions.resolve(conn.client, _target(c), need_join=False)
        full = await conn.client(GetFullChannelRequest(peer))
        return full.full_chat.call

    async def _drop(self, sid: str, leave: bool = False) -> None:
        conn = self.conns.pop(sid, None)
        if not conn:
            return
        self.by_channel.get(conn.cid, set()).discard(sid)
        if leave:
            try:
                await conn.app.leave_call(conn.cid)
            except Exception:  # noqa: BLE001
                pass
        with __import__("contextlib").suppress(Exception):
            await self.s.close_client(conn.client, sid)
        self.alloc.release(sid)
        await db.delete(f"live/{sid}")

    async def _leave_channel(self, cid: str) -> None:
        for sid in list(self.by_channel.get(cid, ())):
            await self._drop(sid, leave=True)
        self.by_channel.pop(cid, None)

    def info(self) -> dict:
        return {"available": self.available, "connections": len(self.conns), "channels": len(self.by_channel)}
