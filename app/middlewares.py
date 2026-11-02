from __future__ import annotations

import time

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from . import keyboards as kb
from .config import cfg
from .services import forcejoin, store


class BanMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and user.id in store.BANNED and user.id not in cfg.admin_ids:
            msg = "🚫 You are banned from using this bot."
            if isinstance(event, CallbackQuery):
                await event.answer(msg, show_alert=True)
            else:
                await event.answer(msg)
            return None
        return await handler(event, data)


class ThrottleMiddleware(BaseMiddleware):
    """Drops spammy taps/messages so a single user can never hog the bot."""

    def __init__(self) -> None:
        self._last: dict[int, float] = {}

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and user.id not in cfg.admin_ids:
            gap = 0.35 if isinstance(event, CallbackQuery) else 0.5
            t = time.monotonic()
            if t - self._last.get(user.id, 0) < gap:
                if isinstance(event, CallbackQuery):
                    await event.answer()
                return None
            self._last[user.id] = t
            if len(self._last) > 20000:
                self._last.clear()
        return await handler(event, data)


class ForceJoinMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user or user.id in cfg.admin_ids:
            return await handler(event, data)
        if isinstance(event, Message) and (event.text or "").startswith(("/start", "/cancel")):
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and event.data == "joined":
            return await handler(event, data)
        bot = data["bot"]
        if await forcejoin.is_joined(bot, user.id):
            return await handler(event, data)
        fc = await forcejoin.get_force_channel()
        text, markup = forcejoin.join_text(fc), kb.join_kb(fc["link"])
        if isinstance(event, CallbackQuery):
            await event.answer("📢 Please join our channel first", show_alert=True)
            if event.message:
                await event.message.answer(text, reply_markup=markup)
        else:
            await event.answer(text, reply_markup=markup)
        return None
