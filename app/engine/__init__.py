from __future__ import annotations

import asyncio
import logging

from .. import db
from ..services import store
from .allocator import Allocator
from .campaigns import Campaigns
from .sessions import SessionStore
from .stream import StreamHub
from .watcher import Watcher

log = logging.getLogger("engine")


class Engine:
    def __init__(self, bot) -> None:
        self.bot = bot
        self.sessions = SessionStore()
        self.alloc = Allocator(self.sessions)
        self.campaigns = Campaigns(bot, self.sessions, self.alloc)
        self.stream = StreamHub(self.sessions, self.alloc)
        self.watcher = Watcher(self.sessions, self.campaigns.on_new_post)

    async def start(self) -> None:
        await self.sessions.load()
        await self.watcher.start()
        await self.campaigns.start()
        await self.stream.start(self.bot)
        log.info("engine started (sessions=%d, pytgcalls=%s)", len(self.sessions.meta), self.stream.available)

    async def stop(self) -> None:
        await self.watcher.stop()
        await self.campaigns.stop()
        await self.stream.stop()

    def apply_settings(self, safety: dict) -> None:
        self.sessions.sem = asyncio.Semaphore(int(safety["max_clients"]))

    def info(self) -> dict:
        return {"sessions": self.sessions.counts(), "stream": self.stream.info(),
                "watching_channels": len(self.watcher.chats)}
