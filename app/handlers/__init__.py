from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from . import admin_roles, channels, settings_ui, sessions_bot, start

fallback = Router(name="fallback")


@fallback.callback_query()
async def _expired_button(cb: CallbackQuery):
    await cb.answer("⏳ This button has expired. Please use /start.", show_alert=True)


@fallback.message(F.chat.type == "private")
async def _unknown_message(m: Message):
    await m.answer("🤔 I didn't get that. Use the menu below, or /start.", reply_markup=kb.main_menu(m.from_user.id))


def get_routers() -> list[Router]:
    return [admin_roles.router, start.router, channels.router, settings_ui.router, sessions_bot.router, fallback]
