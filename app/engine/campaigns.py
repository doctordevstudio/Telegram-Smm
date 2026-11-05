"""Continuous per-channel campaign engine.

Unlike the old "pay once for a specific post" model, a service here is simply switched on for a
channel for N days; every post that appears in that channel while the service is enabled gets the
configured treatment automatically. This module is the scheduler that makes that happen:

* `subscribers` and `mute` are channel-level, ongoing background actions (not tied to any one post).
* `views`, `reactions` and `poll` react to posts the Watcher detects, after the channel's configured
  process-delay, honouring Night Mode and Wave Mode speed multipliers.
* `stream` is handled separately by `stream.py` (its own hub, since it needs persistent connections).

All of it is stateless between restarts: progress lives in Firebase (`posts/*`, `progress/*`,
`targets/*`), so a restart just resumes where it left off.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import db, runtime
from ..config import cfg
from ..roles import log_action
from ..services import notify, store
from ..utils import esc, now, spawn
from . import actions
from .errors import classify

log = logging.getLogger("campaigns")
TICK = 4
POST_MAX_AGE = 3 * 86400  # same window as store.pending_posts
REACT_FIELD, VIEW_FIELD, POLL_FIELD = "reacts", "views", "poll"


def _f(v, default: float) -> float:
    try:
        x = float(v)
        return x if x == x and abs(x) != float("inf") else default
    except (TypeError, ValueError):
        return default


def _target(c: dict) -> dict:
    return {"chat_id": c.get("chat_id"), "username": c.get("username"), "invite_hash": c.get("invite_hash"),
            "title": c.get("title"), "link": c.get("link")}


def _chan_key(c: dict) -> str:
    return f"c{c['chat_id']}" if c.get("chat_id") else f"h{c.get('invite_hash')}"


def speed_multiplier(settings: dict, tz: str, post_age: float | None) -> float:
    mult = 1.0
    nm = settings.get("night_mode") or {}
    if nm.get("enabled"):
        hour = datetime.now(ZoneInfo(tz)).hour
        a, b = int(nm.get("start_hour", 22)), int(nm.get("end_hour", 6))
        active = (a <= hour or hour < b) if a > b else (a <= hour < b)
        if active:
            mult *= float(nm.get("multiplier", 2))
    if (settings.get("wave_mode") or {}).get("enabled") and post_age is not None:
        if post_age >= 3 * 3600:
            mult *= 2
        elif post_age >= 3600:
            mult *= 1.5
    return mult


def _log_failures(what: str, items: list, results: list) -> None:
    for item, r in zip(items, results):
        if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError):
            log.error("campaign %s failed for %s: %r", what, item.get("id") if isinstance(item, dict) else item, r,
                      exc_info=r)


class Campaigns:
    def __init__(self, bot, sessions, alloc) -> None:
        self.bot, self.s, self.alloc = bot, sessions, alloc
        self._task: asyncio.Task | None = None
        self._processing: set[str] = set()
        self._notified_expired: set[str] = set()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def on_new_post(self, cid: str, chat_id: int, mid: int, has_poll: bool) -> None:
        pass  # the periodic tick (every TICK seconds) picks new posts up; nothing more to do here

    # ───────── main loop ─────────
    async def _loop(self) -> None:
        while True:
            try:
                channels = await store.list_channels()
                res = await asyncio.gather(*(self._tick_channel(c) for c in channels), return_exceptions=True)
                _log_failures("channel tick", channels, res)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("campaign tick failed")
            await asyncio.sleep(TICK)

    async def _tick_channel(self, c: dict) -> None:
        cid = c["id"]
        svcs = await store.get_all_services(cid)
        settings = await store.get_channel_settings(cid)
        sf = await store.get_safety()
        await self._expire_sweep(c, svcs)
        jobs = []
        if svcs["subscribers"]["enabled"]:
            jobs.append(self._tick_subscribers(c, svcs["subscribers"], sf))
        if svcs["mute"]["enabled"]:
            jobs.append(self._tick_mute(c, svcs["mute"], sf))
        if svcs["views"]["enabled"] or svcs["reactions"]["enabled"] or svcs["poll"]["enabled"]:
            jobs.append(self._tick_posts(c, svcs, settings, sf))
        if jobs:
            res = await asyncio.gather(*jobs, return_exceptions=True)
            _log_failures("service tick", [c] * len(res), res)

    async def _expire_sweep(self, c: dict, svcs: dict) -> None:
        cid = c["id"]
        for name, svc in svcs.items():
            if svc["enabled"] and svc["expires_at"] and svc["expires_at"] <= now():
                await db.update(f"services/{cid}/{name}", {"enabled": False, "expired": True})
                key = f"{cid}:{name}"
                if key not in self._notified_expired:
                    self._notified_expired.add(key)
                    store.log_bg("engine", None, "system", "service_expired", f"{name} on {c['title']}", cid)
                    spawn(notify.notify_staff(self.bot, f"⏳ <b>{esc(name.title())}</b> expired for "
                                                        f"<b>{esc(c['title'])}</b>."))

    # ───────── subscribers (continuous, channel-level) ─────────
    async def _tick_subscribers(self, c: dict, svc: dict, sf: dict) -> None:
        cid, cfgd = c["id"], svc["config"]
        prog = await store.get_progress(cid, "subscribers")
        interval = max(1.0, _f(cfgd.get("interval_seconds"), 60))
        if now() - prog.get("last_ts", 0) < interval:
            return
        base = _chan_key(c)
        members = set(await db.keys(f"targets/{base}/subs"))
        target = int(cfgd.get("target_count") or 0)
        if target and len(members) >= target:
            return
        sids = await self.alloc.pick(1, sf=sf, exclude=set(), members=members, always_join=True)
        if not sids:
            return
        pending = list(sids)
        try:
            await store.update_progress(cid, "subscribers", {"last_ts": now()})
            pending.clear()
            await self._do_action(sids[0], "subscribers", c, lambda cl, sid: actions.subscribe(cl, _target(c)), base, "subs")
        finally:
            self._release_all(pending)

    # ───────── mute (continuous, channel-level, daily cap) ─────────
    async def _tick_mute(self, c: dict, svc: dict, sf: dict) -> None:
        from ..utils import day_key
        cid, cfgd = c["id"], svc["config"]
        today = day_key(cfg.timezone)
        prog = await store.get_progress(cid, "mute")
        if prog.get("day") != today:
            prog = {"day": today, "count": 0, "last_ts": 0}
        interval = max(1.0, _f(cfgd.get("speed_seconds"), 30))
        if prog.get("count", 0) >= int(_f(cfgd.get("daily_actions"), 10)) or now() - prog.get("last_ts", 0) < interval:
            return
        base = _chan_key(c)
        members = set(await db.keys(f"targets/{base}/subs"))
        sids = await self.alloc.pick(1, sf=sf, exclude=set(), members=members)
        if not sids:
            return
        mute = random.random() < 0.5
        pending = list(sids)
        try:
            await store.update_progress(cid, "mute", {"day": today, "count": prog.get("count", 0) + 1, "last_ts": now()})
            pending.clear()
            await self._do_action(sids[0], "mute", c, lambda cl, sid: actions.mute_toggle(cl, _target(c), mute, sid not in members), base, "subs")
        finally:
            self._release_all(pending)

    # ───────── views / reactions / poll on every upcoming post ─────────
    async def _tick_posts(self, c: dict, svcs: dict, settings: dict, sf: dict) -> None:
        cid = c["id"]
        raw = db.as_dict(await db.get(f"posts/{cid}"))
        cutoff = now() - POST_MAX_AGE
        for mid_s, p in raw.items():
            if not isinstance(p, dict) or not str(mid_s).isdigit():
                continue
            if p.get("detected_at", now()) < cutoff:
                continue  # old posts are never delivered to (also keeps this per-tick loop from growing forever)
            mid = int(mid_s)
            lock = f"{cid}:{mid}"
            if lock in self._processing:
                continue
            age = now() - p.get("detected_at", now())
            if age < int(settings.get("delay_seconds", 0)):
                continue
            self._processing.add(lock)
            try:
                await self._process_post(c, cid, mid, p, svcs, settings, sf)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001  one bad post must not block the channel's other posts
                log.exception("campaign: post %s failed", lock)
            finally:
                self._processing.discard(lock)

    async def _process_post(self, c: dict, cid: str, mid: int, p: dict, svcs: dict, settings: dict, sf: dict) -> None:
        age = now() - p.get("detected_at", now())
        mult = speed_multiplier(settings, cfg.timezone, age)
        base = _chan_key(c)
        members = set(await db.keys(f"targets/{base}/subs"))
        jobs = []
        if svcs["views"]["enabled"]:
            jobs.append(self._deliver_views(c, cid, mid, p, svcs["views"]["config"], sf, mult, members, base))
        if svcs["reactions"]["enabled"]:
            jobs.append(self._deliver_reactions(c, cid, mid, p, svcs["reactions"]["config"], sf, mult, members, base))
        if svcs["poll"]["enabled"] and p.get("has_poll"):
            jobs.append(self._deliver_poll(c, cid, mid, p, svcs["poll"]["config"], sf, mult, members, base))
        if jobs:
            res = await asyncio.gather(*jobs, return_exceptions=True)
            _log_failures("post job", [f"{cid}:{mid}"] * len(res), res)

    async def _due_slots(self, cid: str, mid: int, field: str, speed: float, batch_max: int) -> tuple[int, dict]:
        meta = await db.get(f"posts/{cid}/{mid}/_tick/{field}") or {}
        elapsed = now() - meta.get("ts", 0)
        slots = min(batch_max, max(1, int(elapsed / max(speed, 0.5))) if meta.get("ts") else 1)
        await db.put(f"posts/{cid}/{mid}/_tick/{field}", {"ts": now()})
        return slots, meta

    async def _deliver_views(self, c, cid, mid, p, cfgd, sf, mult, members, base) -> None:
        qty = int(cfgd.get("qty", 30))
        if cfgd.get("random_enabled"):
            qty = max(1, qty + random.randint(-int(cfgd.get("random_range", 0)), int(cfgd.get("random_range", 0))))
        key = f"{base}_m{mid}_{VIEW_FIELD}"
        used = set(await db.keys(f"targets/{key}"))
        if len(used) >= qty:
            return
        slots, _ = await self._due_slots(cid, mid, "views", float(cfgd.get("speed_seconds", 5)) * mult, int(sf["batch_max"]))
        n = min(slots, qty - len(used))
        sids = await self.alloc.pick(n, sf=sf, exclude=used, members=members)
        pending = list(sids)  # sessions we still own; _do_action() takes over (and releases) each one it is given
        try:
            for sid in sids:
                pending.remove(sid)
                await self._do_action(sid, "views", c, lambda cl, s, m=mid: actions.view(cl, _target(c), m, s not in members), key, None, also_base=base)
        finally:
            self._release_all(pending)

    async def _deliver_reactions(self, c, cid, mid, p, cfgd, sf, mult, members, base) -> None:
        from ..utils import pick_weighted_emoji
        from ..constants import REACTION_EMOJIS
        qty = int(cfgd.get("qty", 20))
        key = f"{base}_m{mid}_{REACT_FIELD}"
        used = set(await db.keys(f"targets/{key}"))
        if len(used) >= qty:
            return
        # Note: "skip poster" only matters when the poster could be one of our own rented sessions,
        # which never happens here (our sessions only react/view, they never post) - the flag is kept
        # for parity with the reference panel but has no effect on delivery.
        pool = c.get("reactions_allowed") or REACTION_EMOJIS
        slots, _ = await self._due_slots(cid, mid, "reacts", float(cfgd.get("speed_seconds", 15)) * mult, int(sf["batch_max"]))
        n = min(slots, qty - len(used))
        sids = await self.alloc.pick(n, sf=sf, exclude=used, members=members)
        pending = list(sids)
        try:
            for sid in sids:
                emoji = pick_weighted_emoji(cfgd.get("mix") or {}, pool)
                pending.remove(sid)
                await self._do_action(sid, "reactions", c, lambda cl, s, m=mid, e=emoji: actions.react(cl, _target(c), m, e, s not in members), key, None, also_base=base)
        finally:
            self._release_all(pending)

    async def _deliver_poll(self, c, cid, mid, p, cfgd, sf, mult, members, base) -> None:
        max_votes = int(cfgd.get("max_votes") or 0)
        key = f"{base}_m{mid}_{POLL_FIELD}"
        used = set(await db.keys(f"targets/{key}"))
        if max_votes and len(used) >= max_votes:
            return
        cap = (max_votes - len(used)) if max_votes else int(sf["batch_max"])
        slots, _ = await self._due_slots(cid, mid, "poll", float(cfgd.get("speed_seconds", 30)) * mult, int(sf["batch_max"]))
        n = min(slots, cap) if cap > 0 else slots
        sids = await self.alloc.pick(n, sf=sf, exclude=used, members=members)
        pending = list(sids)
        try:
            for sid in sids:
                pending.remove(sid)
                await self._do_action(sid, "poll", c, lambda cl, s, m=mid: actions.vote_poll(cl, _target(c), m, s not in members), key, None, also_base=base)
        finally:
            self._release_all(pending)

    def _release_all(self, sids: list[str]) -> None:
        for sid in sids:
            self.alloc.release(sid)  # idempotent: sessions already released by _do_action are a no-op

    # ───────── shared action runner (mirrors the old Runner._act, generalised) ─────────
    async def _do_action(self, sid: str, kind: str, c: dict, fn, key: str | None, field: str | None, also_base: str | None = None) -> None:
        async def run() -> None:
            try:
                async with self.s.client(sid) as cl:
                    info = await fn(cl, sid)
                ts = now()
                writes = {}
                if key and field:
                    writes[f"targets/{key}/{field}/{sid}"] = ts
                elif key:
                    writes[f"targets/{key}/{sid}"] = ts
                if also_base:
                    writes[f"targets/{also_base}/subs/{sid}"] = ts
                if writes:
                    await db.multi_update(writes)
                self.s.record(sid, join=info.get("joined", False) if isinstance(info, dict) else False)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                k, val, note = classify(e)
                if k == "flood":
                    self.s.mark(sid, flood_until=now() + val + random.randint(10, 60), note=note)
                elif k == "dead":
                    self.s.mark(sid, status="dead", note=note)
                elif k == "limited":
                    self.s.mark(sid, flood_until=now() + val, note=note)
                elif k == "full":
                    self.s.mark(sid, full=True, note=note)
                elif k == "already":
                    if also_base:
                        await db.put(f"targets/{also_base}/subs/{sid}", now())
                elif k == "retry":
                    self.s.bump_fail(sid, note)
                else:
                    log.info("campaign action (%s, %s) failed: %s", kind, c.get("id"), note)
            finally:
                self.alloc.release(sid)
        await run()
