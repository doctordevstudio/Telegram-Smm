"""Live-stream viewers (Phase 3).

* Sessions only JOIN the group call (muted, no video, no media download) and are kept alive with tiny
  `CheckGroupCall` pings -> almost zero bandwidth on Render.
* An account can sit in ONE call at a time, so availability is global (see availability.get_available).
* Several users can buy the same stream at once: every join goes through the Allocator lock + `conns`
  exclusion, so the same session is never given to two orders.
* EXPERIMENTAL: joining uses synthetic WebRTC params (no audio/video transport). Test with 1-2 sessions
  on a real stream before selling it - Telegram can change how it counts such participants at any time.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from dataclasses import dataclass, field

from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.phone import (CheckGroupCallRequest, GetGroupCallRequest, JoinGroupCallRequest,
                                         LeaveGroupCallRequest)
from telethon.tl.types import DataJSON, InputPeerSelf

from .. import db
from ..services.availability import channel_key, target_key
from ..utils import now
from . import actions
from .errors import StreamNotLive

log = logging.getLogger("stream")


@dataclass
class Conn:
    sid: str
    client: object
    call: object
    ssrc: int
    oid: str
    tkey: str
    invite: str | None = None
    fails: int = 0
    joined_at: int = field(default_factory=now)


def _params(ssrc: int) -> str:
    fp = ":".join(f"{b:02X}" for b in os.urandom(32))
    return json.dumps({"ufrag": os.urandom(4).hex(), "pwd": os.urandom(12).hex(), "ssrc": ssrc, "ssrc-groups": [],
                       "fingerprints": [{"hash": "sha-256", "setup": "active", "fingerprint": fp}],
                       "payload-types": [], "rtp-hdrexts": []})


class StreamHub:
    def __init__(self, sessions, alloc) -> None:
        self.s, self.alloc = sessions, alloc
        self.conns: dict[str, Conn] = {}
        self.by_order: dict[str, set[str]] = {}
        self._ka: asyncio.Task | None = None

    async def start(self) -> None:
        await db.delete("live")  # stale entries from a previous process
        self._ka = asyncio.create_task(self._keepalive())

    async def stop(self) -> None:
        if self._ka:
            self._ka.cancel()
        for oid in list(self.by_order):
            await self._leave_order(oid)

    # ───────── joining ─────────
    async def _join_one(self, o: dict, sid: str, members: set[str]) -> dict:
        t, oid = o["target"], o["id"]
        tkey, base = target_key("stream", t), channel_key(t)
        c = await self.s.open_client(sid)
        try:
            peer, joined, pid = await actions.resolve(c, t, need_join=sid not in members)
            actions._check_id(t, pid)
            call = getattr(getattr(await c(GetFullChannelRequest(peer)), "full_chat", None), "call", None)
            if call is None:
                raise StreamNotLive("The channel isn't live right now")
            ssrc = random.randint(10 ** 8, 2 ** 31 - 1)
            invite = t.get("stream") if t.get("stream") not in (None, "", "live") else None
            await c(JoinGroupCallRequest(call=call, join_as=InputPeerSelf(), params=DataJSON(data=_params(ssrc)),
                                         muted=True, video_stopped=True, invite_hash=invite))
        except BaseException:
            await self.s.close_client(c, sid)
            raise
        self.conns[sid] = Conn(sid, c, call, ssrc, oid, tkey, invite)
        self.by_order.setdefault(oid, set()).add(sid)
        await db.multi_update({f"live/{sid}": {"o": oid, "t": tkey}, f"targets/{base}/subs/{sid}": now()})
        self.s.record(sid, join=joined)
        return {"joined": joined}

    async def run_order(self, runner, o: dict, sf: dict, speed: dict) -> None:
        oid, qty, t = o["id"], int(o["qty"]), o["target"]
        base = channel_key(t)
        started = o["started_at"]
        wait_deadline = started + int(sf["stream_wait_minutes"]) * 60
        end_deadline = started + int(sf["stream_max_minutes"]) * 60
        if o.get("delivered") and oid not in self.by_order:  # restarted: previous connections are gone
            o["delivered"] = 0
            await db.update(f"orders/{oid}", {"delivered": 0})
        stall = None
        while True:
            if oid in runner.cancel_flags:
                runner.cancel_flags.discard(oid)
                await self._leave_order(oid)
                return await runner.finish(o, "cancelled", "Cancelled by admin")
            if now() > end_deadline:
                break
            delivered = o.get("delivered", 0)
            if delivered < qty and len(self.conns) < int(sf["stream_cap"]):
                members = set(await db.keys(f"targets/{base}/subs"))
                n = min(qty - delivered, int(sf["stream_concurrency"]), int(sf["stream_cap"]) - len(self.conns))
                sids = await self.alloc.pick(n, sf=sf, exclude=set(self.conns), members=members)
                if not sids:
                    stall = stall or now()
                    if now() - stall > int(sf["stall_minutes"]) * 60 or (
                            not [s for s, m in self.s.meta.items() if m.get("status") == "active" and s not in self.conns]
                            and now() - stall > 60):
                        break
                    await asyncio.sleep(10)
                    continue
                stall = None
                res = await asyncio.gather(*(self._joiner(runner, o, sid, members, i) for i, sid in enumerate(sids)))
                o["delivered"] = delivered + sum(1 for r in res if r["ok"])
                await db.update(f"orders/{oid}", {"delivered": o["delivered"]})
                fatal = next((r for r in res if r["kind"] == "target"), None)
                if fatal:
                    if fatal["exc"] == "StreamNotLive" and not o["delivered"] and now() < wait_deadline:
                        await asyncio.sleep(20)  # wait for the host to go live
                        continue
                    note = fatal["note"] if fatal["exc"] != "StreamNotLive" else "The stream wasn't live in time"
                    await self._leave_order(oid)
                    return await runner.finish(o, "failed", note)
                await asyncio.sleep(random.uniform(0.7, 1.3) * float(speed["stream"]))
                continue
            # everyone joined (or cap reached): watch the stream, stay connected until it ends
            if not await self._alive(oid):
                break
            await asyncio.sleep(30)
        await self._leave_order(oid)
        await runner.finish(o, "completed" if o.get("delivered", 0) >= qty else "partial",
                            "" if o.get("delivered", 0) >= qty else "Stream ended / not enough free sessions")

    async def _joiner(self, runner, o, sid, members, i) -> dict:
        res = {"sid": sid, "ok": False, "kind": "retry", "note": "", "exc": ""}
        try:
            await asyncio.sleep(i * random.uniform(0.4, 1.2))
            await self._join_one(o, sid, members)
            res.update(ok=True, kind="ok")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            kind, note = runner.handle_session_error(sid, e)
            res.update(kind=kind, note=note, exc=type(e).__name__)
        finally:
            if not res["ok"]:
                self.alloc.release(sid)
        return res

    # ───────── monitoring ─────────
    async def _alive(self, oid: str) -> bool:
        sids = list(self.by_order.get(oid, ()))
        if not sids:
            return True
        conn = self.conns.get(sids[0])
        if not conn:
            return True
        try:
            r = await conn.client(GetGroupCallRequest(call=conn.call, limit=1))
            return type(r.call).__name__ != "GroupCallDiscarded"
        except Exception as e:  # noqa: BLE001
            return type(e).__name__ not in ("GroupcallInvalidError", "GroupcallForbiddenError")

    async def _keepalive(self) -> None:
        while True:
            await asyncio.sleep(25)
            for sid, conn in list(self.conns.items()):
                try:
                    active = await conn.client(CheckGroupCallRequest(call=conn.call, sources=[conn.ssrc]))
                    if conn.ssrc not in (active or []):  # kicked out -> rejoin with a fresh ssrc
                        conn.ssrc = random.randint(10 ** 8, 2 ** 31 - 1)
                        await conn.client(JoinGroupCallRequest(call=conn.call, join_as=InputPeerSelf(),
                                                               params=DataJSON(data=_params(conn.ssrc)), muted=True,
                                                               video_stopped=True, invite_hash=conn.invite))
                    conn.fails = 0
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    conn.fails += 1
                    if conn.fails >= 4 or type(e).__name__ in ("GroupcallInvalidError", "AuthKeyUnregisteredError"):
                        log.info("dropping stream conn %s: %s", sid, type(e).__name__)
                        await self._drop(sid)
                await asyncio.sleep(0.4)

    async def _drop(self, sid: str, leave: bool = False) -> None:
        conn = self.conns.pop(sid, None)
        if not conn:
            return
        self.by_order.get(conn.oid, set()).discard(sid)
        if leave:
            try:
                await conn.client(LeaveGroupCallRequest(call=conn.call, source=conn.ssrc))
            except Exception:  # noqa: BLE001
                pass
        await self.s.close_client(conn.client, sid)
        self.alloc.release(sid)
        await db.delete(f"live/{sid}")

    async def _leave_order(self, oid: str) -> None:
        for sid in list(self.by_order.get(oid, ())):
            await self._drop(sid, leave=True)
        self.by_order.pop(oid, None)
