"""Session manager: import / validate / encrypt-at-rest / safe Telegram client factory / API-id pool."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
from contextlib import asynccontextmanager, suppress

from telethon import TelegramClient
from telethon.sessions import StringSession

from .. import db
from ..security import dec, enc
from ..services import store
from ..utils import now, spawn
from . import sessionfmt
from .errors import classify

log = logging.getLogger("sessions")

DEVICES = [("Samsung Galaxy S22", "SDK 33", "10.9.1"), ("Xiaomi 12", "SDK 33", "10.8.3"), ("Google Pixel 7", "SDK 34", "10.9.1"),
           ("OnePlus 10 Pro", "SDK 33", "10.8.1"), ("Samsung Galaxy A52", "SDK 31", "10.6.2"), ("Realme 9 Pro", "SDK 32", "10.7.0")]


class NoApiError(Exception):
    pass


def _proxy(url: str | None):
    if not url:
        return None
    m = re.match(r"^(socks5|socks4|http)://(?:([^:@]+):([^@]*)@)?([^:/]+):(\d+)$", url.strip())
    if not m:
        return None
    typ, user, pw, host, port = m.groups()
    return (typ, host, int(port), True, user, pw) if user else (typ, host, int(port), True)


class SessionStore:
    def __init__(self) -> None:
        self.meta: dict[str, dict] = {}
        self.use: dict[str, dict] = {}  # sid -> {"a": [ts...], "j": [ts...], "last": ts}
        self.busy: set[str] = set()
        self.sem = asyncio.Semaphore(4)
        self.validation = {"running": False, "done": 0, "total": 0, "active": 0, "dead": 0, "limited": 0}
        self._fails: dict[str, int] = {}

    # ───────── load / counters ─────────
    async def load(self) -> None:
        raw = await db.get("sessions", {}) or {}
        self.meta = {k: v for k, v in raw.items() if isinstance(v, dict) and not v.get("hidden")}
        for sid, m in self.meta.items():
            self.use[sid] = {"a": list((m.get("use") or {}).get("a", [])), "j": list((m.get("use") or {}).get("j", [])),
                             "last": (m.get("use") or {}).get("last", 0)}
        self.sem = asyncio.Semaphore(int((await store.get_safety())["max_clients"]))
        await self.recount()
        log.info("loaded %d sessions", len(self.meta))

    def counts(self) -> dict:
        t = now()
        c = {"total": len(self.meta), "active": 0, "dead": 0, "limited": 0, "flood": 0, "new": 0}
        for m in self.meta.values():
            st = m.get("status", "new")
            if st == "active" and m.get("flood_until", 0) > t:
                c["flood"] += 1
            elif st in c:
                c[st] += 1
        return c

    async def recount(self) -> None:
        n = sum(1 for m in self.meta.values() if m.get("status") == "active")
        await db.put("stats/sessions_active", n)
        db.invalidate("stats/sessions_active")

    def mark(self, sid: str, **patch) -> None:
        if sid not in self.meta:
            return
        self.meta[sid].update(patch)
        spawn(db.update(f"sessions/{sid}", patch))
        if "status" in patch:
            spawn(self.recount())

    # ───────── usage / safety bookkeeping ─────────
    def usage(self, sid: str) -> dict:
        return self.use.setdefault(sid, {"a": [], "j": [], "last": 0})

    def record(self, sid: str, join: bool = False) -> None:
        u, t = self.usage(sid), now()
        u["a"] = [x for x in u["a"] if t - x < 3600] + [t]
        u["j"] = [x for x in u["j"] if t - x < 86400] + ([t] if join else [])
        u["last"] = t
        self._fails[sid] = 0
        spawn(db.put(f"sessions/{sid}/use", {"a": u["a"][-40:], "j": u["j"][-60:], "last": t}))

    def bump_fail(self, sid: str, note: str) -> None:
        self._fails[sid] = self._fails.get(sid, 0) + 1
        if self._fails[sid] >= 5:  # repeated unknown failures -> rest this session for a while
            self.mark(sid, flood_until=now() + 1800, note=f"auto-rested: {note}"[:150])
            self._fails[sid] = 0

    # ───────── API id / hash pool ─────────
    async def pick_api(self) -> dict:
        apis = await store.get_apis()
        if not apis:
            raise NoApiError("Add at least one API ID / API hash in Settings first")
        load = {a["id"]: 0 for a in apis}
        for m in self.meta.values():
            if m.get("api_id") in load:
                load[m["api_id"]] += 1
        return min(apis, key=lambda a: load[a["id"]])  # least-loaded -> sessions are spread evenly

    async def api_for(self, m: dict) -> dict:
        apis = await store.get_apis()
        for a in apis:
            if a["id"] == m.get("api_id"):
                return a
        if not apis:
            raise NoApiError("No API ID configured")
        return apis[0]  # original api was removed from settings

    # ───────── clients ─────────
    def _make(self, string: str, api: dict, sid: str, proxy: str | None = None) -> TelegramClient:
        dev = DEVICES[int(hashlib.md5(sid.encode()).hexdigest(), 16) % len(DEVICES)]
        kw = {"proxy": _proxy(proxy)} if _proxy(proxy) else {}
        return TelegramClient(StringSession(string), api["id"], api["hash"], device_model=dev[0], system_version=dev[1],
                              app_version=dev[2], lang_code="en", system_lang_code="en-US", receive_updates=False,
                              connection_retries=2, retry_delay=2, request_retries=2, flood_sleep_threshold=0,
                              timeout=15, **kw)

    async def effective_proxy(self, m: dict) -> str | None:
        """A session's own proxy overrides the global rotating-residential proxy set in Setting / Proxy."""
        if m.get("proxy"):
            return m["proxy"]
        p = await store.get_proxy()
        return p["url"] if p.get("enabled") and p.get("url") else None

    async def open_client(self, sid: str) -> TelegramClient:
        """Connected client (caller must close_client). Used directly by the live-stream hub."""
        m = self.meta[sid]
        string = dec(await db.get(f"session_data/{sid}"))
        c = self._make(string, await self.api_for(m), sid, await self.effective_proxy(m))
        c._smm_string = string  # type: ignore[attr-defined]
        await asyncio.wait_for(c.connect(), 30)
        return c

    async def close_client(self, c: TelegramClient, sid: str | None = None) -> None:
        if sid:
            with suppress(Exception):
                new = c.session.save()
                if new and new != getattr(c, "_smm_string", new):  # DC migration changed the auth key -> keep it
                    await db.put(f"session_data/{sid}", enc(new))
        with suppress(Exception):
            await asyncio.wait_for(c.disconnect(), 10)

    @asynccontextmanager
    async def client(self, sid: str):
        async with self.sem:  # caps simultaneous connections -> bounded RAM on the free tier
            c = await self.open_client(sid)
            try:
                yield c
            finally:
                await self.close_client(c, sid)

    # ───────── import ─────────
    async def import_string(self, string: str, label: str = "", src: str = "string") -> dict:
        try:
            string = string.strip()
            sessionfmt.decode_string(string)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"Invalid session string ({e})"}
        try:
            api = await self.pick_api()
        except NoApiError as e:
            return {"ok": False, "error": str(e)}
        sid = f"s{await db.next_id('sessions'):06d}"
        c = self._make(string, api, sid, await self.effective_proxy({}))
        try:
            async with self.sem:
                await asyncio.wait_for(c.connect(), 30)
                if not await c.is_user_authorized():
                    return {"ok": False, "error": "Session is not logged in / was terminated"}
                me = await c.get_me()
                new = c.session.save()
        except Exception as e:  # noqa: BLE001
            kind, _, note = classify(e)
            return {"ok": False, "error": f"Session rejected by Telegram: {note}"}
        finally:
            await self.close_client(c)
        if any(m.get("user_id") == me.id for m in self.meta.values()):
            return {"ok": False, "error": f"Duplicate - account {me.id} is already imported"}
        meta = {"id": sid, "label": label or (me.first_name or "")[:40], "phone": me.phone or "", "user_id": me.id,
                "username": me.username or "", "api_id": api["id"], "status": "active", "src": src,
                "added_at": now(), "last_check": now()}
        await db.multi_update({f"sessions/{sid}": meta, f"session_data/{sid}": enc(new or string)})
        self.meta[sid] = meta
        self.use[sid] = {"a": [], "j": [], "last": 0}
        await self.recount()
        return {"ok": True, "id": sid, "username": meta["username"], "phone": meta["phone"]}

    async def import_file(self, filename: str, raw: bytes) -> dict:
        try:
            if filename.lower().endswith((".txt", ".string")):
                return await self.import_string(raw.decode("utf-8", "ignore"), src="file")
            string = sessionfmt.from_sqlite_bytes(raw)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return await self.import_string(string, label=filename.rsplit(".", 1)[0][:40], src="file")

    # ───────── validate / delete ─────────
    async def validate_one(self, sid: str) -> str:
        if sid not in self.meta:
            return "missing"
        try:
            async with self.client(sid) as c:
                if not await c.is_user_authorized():
                    raise_dead = True
                else:
                    raise_dead = False
                    me = await c.get_me()
            if raise_dead:
                self.mark(sid, status="dead", note="Not authorized (session terminated)", last_check=now())
                return "dead"
            self.mark(sid, status="active", note="", last_check=now(), phone=me.phone or "", username=me.username or "")
            return "active"
        except Exception as e:  # noqa: BLE001
            kind, val, note = classify(e)
            if kind == "dead":
                self.mark(sid, status="dead", note=note, last_check=now())
                return "dead"
            if kind in ("limited", "flood"):
                self.mark(sid, flood_until=now() + (val or 3600), note=note, last_check=now())
                return "limited"
            self.mark(sid, note=f"check failed: {note}", last_check=now())
            return "error"

    async def validate_many(self, ids: list[str]) -> None:
        if self.validation["running"]:
            return
        v = self.validation
        v.update(running=True, done=0, total=len(ids), active=0, dead=0, limited=0, error=0)
        sem = asyncio.Semaphore(3)

        async def one(sid: str) -> None:
            async with sem:
                r = await self.validate_one(sid)
                v[r if r in ("active", "dead", "limited") else "error"] = v.get(r if r in ("active", "dead", "limited") else "error", 0) + 1
                v["done"] += 1
                await asyncio.sleep(random.uniform(0.5, 1.5))  # gentle on Telegram

        try:
            await asyncio.gather(*(one(s) for s in ids))
        finally:
            v["running"] = False
            await self.recount()

    async def delete(self, sid: str, by: int | None = None) -> bool:
        """Soft-delete only: the session record (and its encrypted data) stays in the database
        forever, it is just hidden from the pool and from the panel/bot lists."""
        if sid not in self.meta or sid in self.busy:
            return False
        self.meta.pop(sid, None)
        self.use.pop(sid, None)
        await db.update(f"sessions/{sid}", {"hidden": True, "hidden_at": now(), "hidden_by": by, "status": "removed"})
        await self.recount()
        return True
