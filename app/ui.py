from __future__ import annotations

from contextlib import suppress

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message


def msg_of(ev: Message | CallbackQuery):
    return ev.message if isinstance(ev, CallbackQuery) else ev


async def show(ev: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None, *, edit: bool = True):
    """Edit the tapped message in place (clean chat); otherwise send a new one."""
    msg = msg_of(ev)
    if msg is None or not hasattr(msg, "edit_text"):
        return await ev.bot.send_message(ev.from_user.id, text, reply_markup=markup)
    if isinstance(ev, CallbackQuery) and edit:
        try:
            return await msg.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                return msg
            with suppress(Exception):
                await msg.delete()
    return await msg.answer(text, reply_markup=markup)
