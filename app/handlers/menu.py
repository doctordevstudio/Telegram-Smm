"""Bottom reply-keyboard buttons. Registered BEFORE the state handlers so a menu
tap always works, even in the middle of an order/deposit flow."""
from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from .. import keyboards as kb
from .. import ui
from ..config import cfg
from ..constants import (BTN_CHANNEL, BTN_HISTORY, BTN_HOME, BTN_PLAN, BTN_SUPPORT, BTN_WALLET, SERVICE_ORDER,
                         SERVICES)
from ..services import store
from ..utils import esc, money
from . import channel, common, wallet

router = Router(name="menu")


@router.message(F.text == BTN_HOME)
async def m_home(m: Message, state: FSMContext):
    await common.show_home(m, state, edit=False)


@router.message(F.text == BTN_CHANNEL)
async def m_channel(m: Message, state: FSMContext):
    await channel.show_my_channel(m, state)


@router.message(F.text == BTN_WALLET)
async def m_wallet(m: Message, state: FSMContext):
    await state.clear()
    await wallet.show_wallet(m)


@router.message(F.text == BTN_HISTORY)
async def m_history(m: Message, state: FSMContext):
    await state.clear()
    await wallet.show_history(m, 1)


@router.message(F.text == BTN_PLAN)
async def m_plan(m: Message, state: FSMContext):
    await state.clear()
    await ui.show(m, await plans_text(), kb.HOME_KB)


@router.message(F.text == BTN_SUPPORT)
async def m_support(m: Message, state: FSMContext):
    await state.clear()
    s = await store.get_settings()
    uname = (s.get("support_username") or cfg.support_username or "").lstrip("@")
    if uname:
        text = f"🎧 <b>Customer Support</b>\n\nNeed help? Contact our admin:\n👉 @{esc(uname)}"
        markup = kb.ik([("💬 Contact Admin", f"https://t.me/{uname}")], [("🏠 Home", "home")])
    else:
        text = "🎧 <b>Customer Support</b>\n\nNeed help? Tap below to message our admin."
        markup = kb.ik([("💬 Contact Admin", f"tg://user?id={cfg.admin_ids[0]}")], [("🏠 Home", "home")])
    await m.answer(text, reply_markup=markup)


async def plans_text() -> str:
    data = await asyncio.gather(*(asyncio.gather(store.get_pricing(s), store.get_plans(s)) for s in SERVICE_ORDER))
    parts = ["💎 <b>Our Plans</b>\n"]
    for svc, (pr, plans) in zip(SERVICE_ORDER, data):
        s = SERVICES[svc]
        line = " • ".join(f"{p['qty']:,} = {money(p['price'])}" for p in plans) or "—"
        parts.append(f"{s['emoji']} <b>{s['title']}</b> · {money(pr['unit'])}/{s['unit']}\n{line}\n")
    parts.append("✏️ Need a different amount? Choose <b>Custom Quantity</b> while ordering.")
    return "\n".join(parts)
