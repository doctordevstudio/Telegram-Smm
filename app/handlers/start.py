from __future__ import annotations

from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

from .. import keyboards as kb
from .. import ui
from ..config import cfg
from ..services import forcejoin, notify, store
from ..utils import esc, fmt_ts, now, spawn
from . import common

router = Router(name="start")

HOW_TO = (
    "📖 <b>How it works</b>\n\n"
    "1️⃣ Add your channel 📺 (public or private link)\n"
    "2️⃣ Add money to your Wallet 💰 via UPI\n"
    "3️⃣ Pick a service — Subscribers, Views, Reactions or Live Stream 🚀\n"
    "4️⃣ Choose a plan & confirm payment from your wallet\n"
    "5️⃣ Relax — delivery starts automatically and we notify you ✅"
)


async def _notify_new_user(bot: Bot, u: User) -> None:
    rec = await store.get_user(u.id) or {}
    total = await store.total_users()
    text = (f"🆕 <b>New user started the bot</b>\n\n"
            f"👤 Name: {esc(u.full_name)}\n🆔 Chat ID: <code>{u.id}</code>\n"
            f"🔗 Username: {'@' + esc(u.username) if u.username else '—'}\n"
            f"🕒 {fmt_ts(now(), cfg.timezone)}\n👥 Total users: <b>{total}</b>")
    await notify.notify_admins(bot, text, photo=rec.get("photo"))


async def send_welcome(ev, state: FSMContext) -> None:
    user, msg = ev.from_user, ui.msg_of(ev)
    rec = await store.get_user(user.id) or {}
    name = esc(user.first_name or "there")
    if not rec.get("welcomed"):
        await msg.answer(f"👋 <b>Welcome, {name}!</b>\n\n✨ Grow your Telegram channel the smart way.\n\n{HOW_TO}",
                         reply_markup=kb.main_menu())
        await store.update_user(user.id, {"welcomed": True})
    else:
        await msg.answer(f"👋 Welcome back, <b>{name}</b>!", reply_markup=kb.main_menu())
    if await store.get_channel(user.id):
        await common.show_home(ev, state, edit=False)
    else:
        await common.ask_channel_link(ev, state, edit=False)


@router.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    u = m.from_user
    is_new = await store.upsert_user(bot, u)
    if is_new:
        spawn(_notify_new_user(bot, u))
    store.log_bg("bot", u.id, "start", "new user" if is_new else "")
    if not await forcejoin.is_joined(bot, u.id):
        fc = await forcejoin.get_force_channel()
        return await m.answer(forcejoin.join_text(fc), reply_markup=kb.join_kb(fc["link"]))
    await send_welcome(m, state)


@router.callback_query(F.data == "joined")
async def joined(cb: CallbackQuery, state: FSMContext, bot: Bot):
    if not await forcejoin.is_joined(bot, cb.from_user.id):
        return await cb.answer("❌ You haven't joined yet. Join the channel, then tap again.", show_alert=True)
    await cb.answer("✅ Thanks for joining!")
    if cb.message:
        with suppress(Exception):
            await cb.message.delete()
    await send_welcome(cb, state)


@router.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("❌ Cancelled.", reply_markup=kb.main_menu())
    await common.show_home(m, state, edit=False)


@router.callback_query(F.data == "cancel")
async def cancel_cb(cb: CallbackQuery, state: FSMContext):
    await cb.answer("Cancelled")
    await state.clear()
    await ui.show(cb, "❌ <b>Cancelled</b>\n\nNothing was charged. Pick something else 👇", kb.HOME_KB)


@router.callback_query(F.data == "home")
async def home_cb(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await common.show_home(cb, state)


@router.callback_query(F.data == "howto")
async def howto_cb(cb: CallbackQuery):
    await cb.answer()
    await ui.show(cb, HOW_TO, kb.HOME_ONLY_KB)


@router.callback_query(F.data == "noop")
async def noop(cb: CallbackQuery):
    await cb.answer()
