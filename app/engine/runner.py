"""Order scheduler: turns paid orders into slow, safe, resumable drip-feed jobs.

Every order lives in Firebase (orders/{id}) with its progress, so a Render restart simply resumes it.
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections import defaultdict

from .. import db
from ..constants import SERVICES
from ..services import notify, store
from ..services.availability import FIELD, channel_key, target_key
from ..utils import now, spawn
from . import actions
from .allocator import Allocator
from .errors import classify
from .schedule import build_segments, due_count, wait_hint
from .sessions import SessionStore

log = logging.getLogger("runner")
MAX_PARALLEL_ORDERS = 8


class Runner:
    def __init__(self, bot, sessions: SessionStore, alloc: Allocator, hub) -> None:
        self.bot, self.s, self.alloc, self.hub = bot, sessions, alloc, hub
        self.tasks: dict[str, asyncio.Task] = {}
        self.cancel_flags: set[str] = set()
        self.crashes: dict[str, int] = defaultdict(int)
        self._stop = False

    # ───────── scheduler ─────────
    async def loop(self) -> None:
        while not self._stop:
            try:
                for oid in sorted(await db.keys("idx/active_orders")):
                    if oid not in self.tasks and len(self.tasks) < MAX_PARALLEL_ORDERS:
                        self.kick(oid)
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            await asyncio.sleep(4)

    def kick(self, oid: str) -> None:
        """Start an order right now (called right after payment => 'instant' start)."""
        if oid not in self.tasks:
            self.tasks[oid] = asyncio.create_task(self._guard(oid))

    async def stop(self) -> None:
        self._stop = True
        for t in list(self.tasks.values()):
            t.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    async def cancel(self, oid: str) -> bool:
        o = await store.get_order(oid)
        if not o or o["status"] not in ("queued", "running"):
            return False
        if oid in self.tasks:
            self.cancel_flags.add(oid)
        else:
            await self.finish(o, "cancelled", "Cancelled by admin")
        return True

    async def _guard(self, oid: str) -> None:
        try:
            await self._run(oid)
            self.crashes.pop(oid, None)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self.crashes[oid] += 1
            log.exception("order %s crashed (%d)", oid, self.crashes[oid])
            if self.crashes[oid] >= 5:
                o = await store.get_order(oid)
                if o:
                    await self.finish(o, "failed", f"Internal error: {str(e)[:80]}")
            else:
                await asyncio.sleep(8)
        finally:
            self.tasks.pop(oid, None)

    # ───────── order execution ─────────
    async def _run(self, oid: str) -> None:
        o = await store.get_order(oid)
        if not o or o.get("status") not in ("queued", "running"):
            await db.delete(f"idx/active_orders/{oid}")
            return
        sf, speed = await store.get_safety(), await store.get_speed()
        if o["status"] == "queued":
            patch = {"status": "running", "started_at": now(), "segments": build_segments(o, speed)}
            if o["service"] == "reactions":
                patch["rem"] = [dict(r) for r in o["reactions"]]
            await db.update(f"orders/{oid}", patch)
            o.update(patch)
            store.log_bg("bot", o["user_id"], "order_started", f"#{oid} {o['service']}")
        if o["service"] == "stream":
            return await self.hub.run_order(self, o, sf, speed)
        await self._drip(o, sf)

    async def _keys(self, path: str) -> set[str]:
        return set(await db.keys(path))

    async def _drip(self, o: dict, sf: dict) -> None:
        oid, svc, qty, t = o["id"], o["service"], int(o["qty"]), o["target"]
        tkey, base, field = target_key(svc, t), channel_key(t), FIELD[svc]
        stall_since = None
        while True:
            if oid in self.cancel_flags:
                self.cancel_flags.discard(oid)
                return await self.finish(o, "cancelled", "Cancelled by admin")
            if o.get("delivered", 0) >= qty:
                return await self.finish(o, "completed")
            deficit = due_count(o["segments"], o["started_at"], now(), qty) - o.get("delivered", 0)
            if deficit <= 0:
                await asyncio.sleep(wait_hint(o["segments"], o["started_at"], now()))
                continue
            used, members = await asyncio.gather(self._keys(f"targets/{tkey}/{field}"), self._keys(f"targets/{base}/subs"))
            sids = await self.alloc.pick(min(deficit, int(sf["batch_max"])), sf=sf, exclude=used, members=members,
                                         always_join=(svc == "subscribers"))
            if not sids:
                stall_since = stall_since or now()
                usable = [s for s, m in self.s.meta.items() if m.get("status") == "active" and s not in used]
                if not usable and now() - stall_since > 90:
                    return await self.finish(o, "partial", "No more unused sessions available for this target")
                if now() - stall_since > int(sf["stall_minutes"]) * 60:
                    return await self.finish(o, "partial", "Sessions were unavailable for too long")
                await asyncio.sleep(15)
                continue
            stall_since = None
            emojis = self._emoji_slots(o, len(sids)) if svc == "reactions" else [None] * len(sids)
            res = await asyncio.gather(*(self._act(o, sid, i, emojis[i], members, sf, tkey, base, field)
                                         for i, sid in enumerate(sids)))
            ok = [r for r in res if r["ok"]]
            o["delivered"] = o.get("delivered", 0) + len(ok)
            patch = {"delivered": o["delivered"]}
            if svc == "reactions":
                for r in ok:
                    for x in o["rem"]:
                        if x["e"] == r["emoji"] and x["n"] > 0:
                            x["n"] -= 1
                            break
                patch["rem"] = o["rem"]
            await db.update(f"orders/{oid}", patch)
            bad = next((r for r in res if r["kind"] == "target"), None)
            if bad:
                return await self.finish(o, "failed", bad["note"])
            await asyncio.sleep(random.uniform(1, 3))

    @staticmethod
    def _emoji_slots(o: dict, n: int) -> list[str]:
        slots = [x["e"] for x in o["rem"] for _ in range(max(0, int(x["n"])))]
        return (slots + [slots[-1] if slots else "👍"] * n)[:n]

    async def _act(self, o, sid, i, emoji, members, sf, tkey, base, field) -> dict:
        svc, t = o["service"], o["target"]
        res = {"sid": sid, "ok": False, "kind": "retry", "note": "", "emoji": emoji, "exc": ""}
        try:
            await asyncio.sleep(i * random.uniform(float(sf["jitter_min"]), float(sf["jitter_max"])))
            need_join = sid not in members
            async with self.s.client(sid) as c:
                if svc == "subscribers":
                    info = await actions.subscribe(c, t)
                elif svc == "views":
                    info = await actions.view(c, t, need_join)
                else:
                    info = await actions.react(c, t, emoji, need_join)
            ts = now()
            await db.multi_update({f"targets/{tkey}/{field}/{sid}": ts, f"targets/{base}/subs/{sid}": ts})
            self.s.record(sid, join=info.get("joined", False))
            res.update(ok=True, kind="ok")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            kind, note = self.handle_session_error(sid, e)
            res.update(kind=kind, note=note, exc=type(e).__name__)
            if kind == "already":
                await db.put(f"targets/{base}/subs/{sid}", now())
                res.update(ok=True, kind="ok")
        finally:
            self.alloc.release(sid)
        return res

    def handle_session_error(self, sid: str, e: BaseException) -> tuple[str, str]:
        kind, val, note = classify(e)
        if kind == "flood":
            self.s.mark(sid, flood_until=now() + val + random.randint(30, 300), note=note)
        elif kind == "dead":
            self.s.mark(sid, status="dead", note=note)
            store.log_bg("admin", None, "session_dead", f"{sid}: {note}")
        elif kind == "limited":
            self.s.mark(sid, flood_until=now() + val, note=note)
        elif kind == "full":
            self.s.mark(sid, full=True, note=note)
        elif kind == "retry":
            self.s.bump_fail(sid, note)
            log.info("session %s: %s", sid, note)
        return kind, note

    # ───────── completion ─────────
    async def finish(self, o: dict, status: str, note: str = "") -> None:
        oid, qty = o["id"], int(o["qty"])
        delivered = min(int(o.get("delivered", 0)), qty)
        if status in ("failed", "partial") and delivered > 0:
            status = "partial"
        elif status == "partial" and delivered == 0:
            status = "failed"
        elif status == "completed" and delivered < qty:
            status = "partial"
        refund = 0.0
        if status in ("failed", "partial", "cancelled") and delivered < qty:
            refund = round(float(o["price"]) * (qty - delivered) / qty, 2)

        def _flip(cur):
            if cur not in ("queued", "running"):
                raise store.AlreadyProcessed(cur)
            return status

        try:
            await db.txn(f"orders/{oid}/status", _flip)
        except store.AlreadyProcessed:
            await db.delete(f"idx/active_orders/{oid}")
            return
        if refund:
            await store.credit(o["user_id"], refund)
            spawn(store.bump_stats(spent=-refund))
            note = (note + f" · Refunded {refund:g} to your wallet").strip(" ·")
        patch = {"finished_at": now(), "note": note, "refund": refund, "delivered": delivered}
        await db.update(f"orders/{oid}", patch)
        await db.delete(f"idx/active_orders/{oid}")
        o.update(patch, status=status)
        store.log_bg("bot", o["user_id"], f"order_{status}", f"#{oid} {delivered}/{qty} {note}"[:250])
        await notify.notify_order_status(self.bot, o)
