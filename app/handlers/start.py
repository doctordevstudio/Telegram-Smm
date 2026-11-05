"""/start, help, and the small bits of housekeeping (new-admin bootstrap notice)."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from .. import keyboards as kb
from ..config import cfg
from ..roles import ensure_owner_record, is_owner, is_staff, role_of
from ..services import store
from . import channels

router = Router(name="start")

HELP = (
    "🆘 <b>Help</b>\n\n"
    "📺 <b>Channels</b> — list every channel you manage\n"
    "➕ <b>Add Channel</b> — add a channel/group, set how many days it stays active\n"
    "📊 <b>Stats</b> — quick engine/session health\n"
    "🧩 <b>Sessions</b> — how many accounts are powering delivery\n"
    "👥 <b>Admins</b> — owner only: grant/revoke bot access\n"
    "🌐 <b>Proxy</b> — owner only: route sessions through your rotating residential proxy\n\n"
    "Open a channel to switch on <b>Subscribers, Views, Reactions, Mute Actions, Poll Voting or Live "
    "Stream Viewers</b> for a chosen number of days — once a service is on, it applies to every post "
    "that appears in that channel from then on, no need to pick a post each time.\n\n"
    "There's no wallet here: you collect payment yourself and just flip the switch."
)


@router.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    u = m.from_user
    if not is_staff(u.id):
        return await m.answer("🚫 This bot is private and only usable by its owner and the admins they've "
                              "granted access to.")
    if is_owner(u.id):
        await ensure_owner_record(u.full_name, u.username or "")
    store.log_bg("bot", u.id, role_of(u.id), "start", "")
    greet = "👑 Welcome back, Owner!" if is_owner(u.id) else f"👋 Welcome, {u.first_name}!"
    await m.answer(f"{greet}\n\n🚀 <b>Telegram SMM Control Panel</b>\n\nManage channels and services from the "
                   f"menu below.", reply_markup=kb.main_menu(u.id))
    await channels.show_list(m, 1)


@router.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("❌ Cancelled.", reply_markup=kb.main_menu(m.from_user.id))
    await channels.show_list(m, 1)


@router.message(F.text == "🆘 Help")
async def m_help(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(HELP)


@router.message(Command("stats"))
@router.message(F.text == "📊 Stats")
async def m_stats(m: Message, state: FSMContext):
    await state.clear()
    from .. import runtime
    eng = runtime.engine.info() if runtime.engine else {}
    sess = eng.get("sessions", {})
    stream = eng.get("stream", {})
    chans = await store.list_channels()
    text = (f"📊 <b>Engine Stats</b>\n\n📺 Channels: <b>{len(chans)}</b>\n\n"
           f"🧩 Sessions — total <b>{sess.get('total', 0)}</b>, active <b>{sess.get('active', 0)}</b>, "
           f"resting <b>{sess.get('flood', 0)}</b>, dead <b>{sess.get('dead', 0)}</b>\n"
           f"👀 Watching for new posts in <b>{eng.get('watching_channels', 0)}</b> channel(s)\n"
           f"🔴 Live-stream connections: <b>{stream.get('connections', 0)}</b>"
           + ("" if stream.get("available", True) else "\n⚠️ pytgcalls unavailable — live stream disabled"))
    await m.answer(text)
