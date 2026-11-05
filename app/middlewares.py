from __future__ import annotations

import time

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from .roles import is_staff


class StaffGateMiddleware(BaseMiddleware):
    """This bot is an internal tool: only the owner and admins the owner has granted access to may use
    it. Everyone else gets a polite rejection (no onboarding, no public flow)."""

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user or is_staff(user.id):
            return await handler(event, data)
        text = "🚫 This bot is private. You don't have access."
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return None


class ThrottleMiddleware(BaseMiddleware):
    """Drops spammy taps/messages so a single user can never hog the bot."""

    def __init__(self) -> None:
        self._last: dict[int, float] = {}

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user:
            gap = 0.3 if isinstance(event, CallbackQuery) else 0.4
            t = time.monotonic()
            if t - self._last.get(user.id, 0) < gap:
                if isinstance(event, CallbackQuery):
                    await event.answer()
                return None
            self._last[user.id] = t
            if len(self._last) > 2000:
                self._last.clear()
        return await handler(event, data)
