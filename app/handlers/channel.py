from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from .. import ui
from ..filters import TextNoCmd
from ..services import resolver, store
from ..states import ChannelSt
from ..utils import esc, parse_tg_link
from . import common

router = Router(name="channel")


def channel_card(ch: dict) -> str:
    private = ch.get("type") == "private"
    members = f"{int(ch['members']):,}" if ch.get("members") is not None else "—"
    status = "⏳ Will be verified on your first order" if not ch.get("verified") else "✅ Verified"
    return (f"📺 <b>My Channel</b>\n\n📛 Name: <b>{esc(ch.get('title'))}</b>\n🔗 Link: {esc(ch.get('link'))}\n"
            f"🔐 Type: {'🔒 Private' if private else '🌐 Public'}\n👥 Members: {members}\n{status}")


async def show_my_channel(ev, state: FSMContext) -> None:
    await state.clear()
    ch = await store.get_channel(ev.from_user.id)
    if not ch:
        return await ui.show(ev, "📺 <b>My Channel</b>\n\nNo channel configured yet.",
                             kb.ik([("➕ Add Channel", "ch:edit")], [("🏠 Home", "home")]))
    await ui.show(ev, channel_card(ch), kb.CHANNEL_KB)


@router.callback_query(F.data == "ch:show")
async def ch_show(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await show_my_channel(cb, state)


@router.callback_query(F.data == "ch:edit")
async def ch_edit(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await common.ask_channel_link(cb, state)


@router.callback_query(F.data == "ch:del")
async def ch_del(cb: CallbackQuery):
    await cb.answer()
    await ui.show(cb, "🗑 <b>Delete channel?</b>\n\nYou can add a new one anytime.", kb.CHANNEL_DELETE_KB)


@router.callback_query(F.data == "ch:del:yes")
async def ch_del_yes(cb: CallbackQuery, state: FSMContext):
    await store.delete_channel(cb.from_user.id)
    store.log_bg("bot", cb.from_user.id, "channel_deleted")
    await cb.answer("🗑 Channel deleted")
    await ui.show(cb, "🗑 <b>Channel deleted.</b>", kb.ik([("➕ Add Channel", "ch:edit")], [("🏠 Home", "home")]))


@router.message(ChannelSt.link, TextNoCmd())
async def got_link(m: Message, state: FSMContext, bot: Bot):
    link = parse_tg_link(m.text)
    if not link:
        return await m.answer("❌ That doesn't look like a Telegram link.\n\nExample: "
                              "<code>https://t.me/yourchannel</code>", reply_markup=kb.CANCEL_KB)
    status = await m.answer("🔍 Checking your channel…")
    res = await resolver.resolve_channel(bot, link)
    if isinstance(res, str):
        return await status.edit_text(res, reply_markup=kb.CANCEL_KB)
    await store.save_channel(m.from_user.id, res)
    store.log_bg("bot", m.from_user.id, "channel_set", res.get("link", ""))
    await state.clear()
    text = "✅ <b>Channel configured!</b>\n\n" + channel_card(res).split("\n\n", 1)[1] + "\n\n🚀 Now pick a service 👇"
    await status.edit_text(text, reply_markup=kb.HOME_KB)
