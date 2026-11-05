"""Watches every managed channel for new posts using one persistent Telethon session, and turns
each new post (or new poll) into a record the campaign engine can act on - replacing the old
"buy for one specific forwarded post" flow with "configure once, applies to every upcoming post".
"""
from __future__ import annotations

import asyncio
import logging

from telethon import events

from ..services import store
from ..utils import now, spawn
from . import actions
from .errors import classify

log = logging.getLogger("watcher")
SYNC_EVERY = 30


def _target_of(c: dict) -> dict:
    return {"chat_id": c.get("chat_id"), "username": c.get("username"), "invite_hash": c.get("invite_hash"),
            "title": c.get("title"), "link": c.get("link")}


class Watcher:
    def __init__(self, sessions, on_new_post) -> None:
        self.s = sessions
        self.on_new_post = on_new_post  # async fn(cid: str, chat_id: int, message_id: int, has_poll: bool)
        self.sid: str | None = None
        self.client = None
        self.chats: set[int] = set()
        self.cid_by_chat: dict[int, str] = {}
        self._task: asyncio.Task | None = None
        self._handler = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self._release()

    async def _release(self) -> None:
        """Drop the watcher's client and give its session back to the pool."""
        client, sid = self.client, self.sid
        self.client, self._handler = None, None
        self.chats, self.cid_by_chat = set(), {}  # a new client has no handlers: force _sync to register them again
        if client:
            await self.s.close_client(client, sid)
        if sid:
            self.s.reserved.discard(sid)
        self.sid = None

    async def _ensure_connected(self) -> bool:
        if self.client and self.client.is_connected():
            sid_meta = self.s.meta.get(self.sid or "")
            if sid_meta and sid_meta.get("status") == "active":
                return True
        if self.client:  # disconnected, or the session died/was removed -> start over with another one
            await self._release()
        candidates = sorted((sid for sid, m in self.s.meta.items()
                             if m.get("status") == "active" and sid not in self.s.busy
                             and sid not in self.s.reserved),
                            key=lambda sid: len(self.s.usage(sid)["a"]))
        if not candidates:
            return False
        self.sid = candidates[0]
        # reserve it: the same auth key must never be used by two connections at once (AUTH_KEY_DUPLICATED),
        # so the allocator must not hand the watcher's session to a campaign action
        if sum(1 for m in self.s.meta.values() if m.get("status") == "active") > 1:
            self.s.reserved.add(self.sid)  # with a single-session pool we must share it, or nothing else could run
        try:
            self.client = await self.s.open_client(self.sid, updates=True)
        except Exception as e:  # noqa: BLE001
            log.warning("watcher: couldn't connect session %s: %s", self.sid, e)
            self.s.reserved.discard(self.sid)
            self.client, self.sid = None, None
            return False
        log.info("watcher: using session %s", self.sid)
        return True

    async def _loop(self) -> None:
        while True:
            try:
                if await self._ensure_connected():
                    await self._sync()
                else:
                    log.info("watcher: no active session available yet - new posts won't be auto-detected")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("watcher sync failed")
            await asyncio.sleep(SYNC_EVERY)

    async def _watched_channels(self) -> list[dict]:
        out = []
        for c in await store.list_channels():
            try:
                svcs = await store.get_all_services(c["id"])
            except Exception:  # noqa: BLE001  one bad channel must not stop the sync
                log.exception("watcher: skipping channel %s", c.get("id"))
                continue
            if any(svcs[s]["enabled"] for s in ("views", "reactions", "poll")):
                out.append(c)
        return out

    async def _sync(self) -> None:
        chans = await self._watched_channels()
        new_chats, new_map = set(), {}
        for c in chans:
            if not c.get("chat_id"):
                c = await self._resolve_missing_chat_id(c)
                if not c or not c.get("chat_id"):
                    continue
            new_chats.add(c["chat_id"])
            new_map[c["chat_id"]] = c["id"]
            if c["chat_id"] not in self.chats:
                await self._join(c)
        if new_chats != self.chats or self._handler is None:
            # add_event_handler() returns None in Telethon, so the old code never removed the previous handler and
            # stacked a new one on every change; remove by callback instead (removes all handlers of that callback)
            self.client.remove_event_handler(self._on_message)
            self.chats, self.cid_by_chat = new_chats, new_map
            self.client.add_event_handler(self._on_message, events.NewMessage(chats=list(self.chats) or [0]))
            self._handler = True
            log.info("watcher: now watching %d channel(s)", len(self.chats))
        else:
            self.cid_by_chat = new_map

    async def _resolve_missing_chat_id(self, c: dict) -> dict | None:
        try:
            _, _, pid = await actions.resolve(self.client, _target_of(c), need_join=False)
        except Exception as e:  # noqa: BLE001
            log.info("watcher: can't resolve channel %s yet: %s", c["id"], e)
            return None
        await store.update_channel(c["id"], {"chat_id": pid})
        c["chat_id"] = pid
        return c

    async def _join(self, c: dict) -> None:
        try:
            await actions.resolve(self.client, _target_of(c), need_join=True)
        except Exception as e:  # noqa: BLE001
            kind, _, note = classify(e)
            if kind != "already":
                log.warning("watcher: couldn't join %s: %s", c["id"], note)

    async def _on_message(self, event) -> None:
        try:
            chat_id = event.chat_id
            cid = self.cid_by_chat.get(chat_id)
            if not cid:
                return
            has_poll = bool(getattr(event.message, "poll", None))
            poster_id = getattr(event.message, "sender_id", None)
            await store.record_post(cid, event.message.id, has_poll, poster_id)
            spawn(self.on_new_post(cid, chat_id, event.message.id, has_poll))
        except Exception:  # noqa: BLE001
            log.exception("watcher: failed to handle new message")
