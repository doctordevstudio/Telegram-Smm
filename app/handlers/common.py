from __future__ import annotations

import asyncio

from aiogram.fsm.context import FSMContext

from .. import keyboards as kb
from .. import ui
from ..services import store
from ..states import ChannelSt
from ..utils import esc, money


async def home_text(uid: int) -> str:
    bal, ch = await asyncio.gather(store.get_balance(uid), store.get_channel(uid))
    chan = f"<b>{esc(ch['title'])}</b>" if ch else "<i>not added yet</i>"
    return (f"🏠 <b>Home</b>\n\n💰 Wallet: <b>{money(bal)}</b>\n📺 Channel: {chan}\n\n"
            f"What would you like to boost today? 👇")


async def show_home(ev, state: FSMContext, *, edit: bool = True) -> None:
    await state.clear()
    await ui.show(ev, await home_text(ev.from_user.id), kb.HOME_KB, edit=edit)


async def ask_channel_link(ev, state: FSMContext, note: str = "", *, edit: bool = True) -> None:
    await state.set_state(ChannelSt.link)
    text = (f"{note}📺 <b>Add Your Channel</b>\n\nSend your channel / group link:\n\n"
            f"🌐 Public: <code>https://t.me/yourchannel</code> or <code>@yourchannel</code>\n"
            f"🔒 Private: invite link <code>https://t.me/+AbCdEf…</code>")
    await ui.show(ev, text, kb.CANCEL_KB, edit=edit)
