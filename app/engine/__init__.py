from __future__ import annotations

import asyncio
import logging

from .. import db
from ..services import store
from .allocator import Allocator
from .runner import Runner
from .sessions import SessionStore
from .stream import StreamHub

log = logging.getLogger("engine")


class Engine:
    def __init__(self, bot) -> None:
        self.sessions = SessionStore()
        self.alloc = Allocator(self.sessions)
        self.hub = StreamHub(self.sessions, self.alloc)
        self.runner = Runner(bot, self.sessions, self.alloc, self.hub)
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await self.sessions.load()
        await self.hub.start()
        self._task = asyncio.create_task(self.runner.loop())
        log.info("engine started")

    async def stop(self) -> None:
        await self.runner.stop()
        await self.hub.stop()
        if self._task:
            self._task.cancel()

    def apply_settings(self, safety: dict) -> None:
        self.sessions.sem = asyncio.Semaphore(int(safety["max_clients"]))

    def info(self) -> dict:
        return {"sessions": self.sessions.counts(), "running_orders": len(self.runner.tasks),
                "live_connections": len(self.hub.conns), "validating": self.sessions.validation["running"]}
