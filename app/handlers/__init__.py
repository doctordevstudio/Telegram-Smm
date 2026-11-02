from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from . import admin, channel, menu, order, start, wallet

fallback = Router(name="fallback")


@fallback.callback_query()
async def _expired_button(cb: CallbackQuery):
    await cb.answer("⏳ This button has expired. Please use the menu or /start.", show_alert=True)


@fallback.message(F.chat.type == "private")
async def _unknown_message(m: Message):
    await m.answer("🤔 I didn't get that. Please use the menu buttons below 👇", reply_markup=kb.main_menu())


def get_routers() -> list[Router]:
    # ORDER MATTERS: admin -> /start + menu buttons (always work) -> flows -> fallback
    return [admin.router, start.router, menu.router, channel.router, order.router, wallet.router, fallback]
