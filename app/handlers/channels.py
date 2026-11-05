"""Add / list / control-panel / renew / revoke-all / remove a channel. No per-post purchase any
more: a channel is added once, then services are switched on for it (see settings_ui.py)."""
from __future__ import annotations

import asyncio

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from .. import ui
from ..constants import PAGE_SIZE_BOT, PRESET_DAYS, SERVICE_ORDER, SERVICES
from ..filters import TextNoCmd
from ..roles import role_of
from ..services import resolver, store
from ..states import ChannelSt
from ..utils import esc, fmt_days_left, fmt_ts, now, paginate, parse_tg_link

router = Router(name="channels")


def _any_active(svcs: dict) -> bool:
    return any(s["enabled"] for s in svcs.values())


async def channel_card(c: dict) -> str:
    svcs = await store.get_all_services(c["id"])
    lines = [f"⚙️ <b>CHANNEL CONTROL PANEL</b>", "",
             f"📛 Name: <b>{esc(c['title'])}</b>", f"🆔 ID: <code>{esc(c.get('chat_id') or c['id'])}</code>",
             f"🔗 Link: {esc(c.get('link', ''))}"]
    if c.get("rate_note"):
        lines.append(f"💰 Rate: {esc(c['rate_note'])}")
    status = "🟢 ACTIVE" if not c.get("expires_at") or c["expires_at"] > now() else "🔴 EXPIRED"
    if c.get("expires_at"):
        status += f" ({fmt_days_left(c['expires_at'])})"
    lines.append(f"⏳ Status: {status}")
    lines.append("")
    lines.append("📋 <b>Active services:</b>")
    any_on = False
    for s in SERVICE_ORDER:
        if svcs[s]["enabled"]:
            any_on = True
            lines.append(f"  {SERVICES[s]['emoji']} {SERVICES[s]['title']} — {fmt_days_left(svcs[s]['expires_at'])}")
    if not any_on:
        lines.append("  <i>none yet — tap a button below to enable one</i>")
    return "\n".join(lines)


async def show_panel(ev, cid: str) -> None:
    c = await store.get_channel(cid)
    if not c:
        return await ui.show(ev, "⚠️ This channel was removed.", kb.HOME_ONLY_KB)
    await ui.show(ev, await channel_card(c), kb.channel_panel_kb(cid))


# ───────────── list ─────────────
async def show_list(ev, page: int) -> None:
    chans = await store.list_channels()
    svcmap = await asyncio.gather(*(store.get_all_services(c["id"]) for c in chans))
    for c, s in zip(chans, svcmap):
        c["_active"] = _any_active(s)
    if not chans:
        return await ui.show(ev, "📺 <b>Channels</b>\n\nNo channels added yet.", kb.ik([("➕ Add Channel", "ch:add")]))
    chunk, page, pages = paginate(chans, page, PAGE_SIZE_BOT)
    await ui.show(ev, f"📺 <b>Channels</b> ({len(chans)})\n\n🟢 = has an active service", kb.channels_list_kb(chunk, page, pages))


@router.message(F.text == "📺 Channels")
async def m_channels(m: Message, state: FSMContext):
    await state.clear()
    await show_list(m, 1)


@router.callback_query(F.data.startswith("ch:list:"))
async def ch_list_cb(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await show_list(cb, int(cb.data.split(":")[2]))


@router.callback_query(F.data.startswith("ch:open:"))
async def ch_open_cb(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await show_panel(cb, cb.data.split(":", 2)[2])


@router.callback_query(F.data == "home")
async def home_cb(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await show_list(cb, 1)


@router.callback_query(F.data == "cancel")
async def cancel_cb(cb: CallbackQuery, state: FSMContext):
    await cb.answer("Cancelled")
    await state.clear()
    await show_list(cb, 1)


@router.message(F.text.in_({"/cancel"}))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("❌ Cancelled.")
    await show_list(m, 1)


@router.callback_query(F.data == "noop")
async def noop(cb: CallbackQuery):
    await cb.answer()


# ───────────── add channel ─────────────
@router.message(F.text == "➕ Add Channel")
async def m_add(m: Message, state: FSMContext):
    await start_add(m, state)


@router.callback_query(F.data == "ch:add")
async def ch_add_cb(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await start_add(cb, state)


async def start_add(ev, state: FSMContext) -> None:
    await state.set_state(ChannelSt.link)
    text = ("➕ <b>Add a Channel</b>\n\nSend the channel / group link:\n\n"
           "🌐 Public: <code>https://t.me/yourchannel</code> or <code>@yourchannel</code>\n"
           "🔒 Private: invite link <code>https://t.me/+AbCdEf…</code>")
    await ui.show(ev, text, kb.CANCEL_KB)


@router.message(ChannelSt.link, TextNoCmd())
async def got_link(m: Message, state: FSMContext, bot: Bot):
    link = parse_tg_link(m.text)
    if not link:
        return await m.answer("❌ That doesn't look like a Telegram link.", reply_markup=kb.CANCEL_KB)
    status = await m.answer("🔍 Checking…")
    res = await resolver.resolve_channel(bot, link)
    if isinstance(res, str):
        return await status.edit_text(res, reply_markup=kb.CANCEL_KB)
    existing = await store.find_channel_by_ref(res.get("chat_id"), res.get("username"), res.get("invite_hash"))
    if existing:
        await state.clear()
        return await status.edit_text(f"ℹ️ This channel is already added as <b>{esc(existing['title'])}</b>.",
                                      kb.ik([("Open it", f"ch:open:{existing['id']}")], [("🏠 Home", "home")]))
    await state.update_data(pending=res)
    await state.set_state(ChannelSt.days)
    await status.edit_text(f"✅ Found <b>{esc(res['title'])}</b>.\n\n📅 How many days should this channel stay "
                           f"active for? (you can change this any time with Renew)", reply_markup=kb.channel_days_kb())


@router.callback_query(ChannelSt.days, F.data.startswith("chdays:"))
async def chdays_cb(cb: CallbackQuery, state: FSMContext):
    val = cb.data.split(":", 1)[1]
    if val == "custom":
        await cb.answer()
        return await ui.show(cb, "✏️ Send the number of days (1-3650):", kb.CANCEL_KB)
    await cb.answer()
    await finalize_channel(cb, state, int(val))


@router.message(ChannelSt.days, TextNoCmd())
async def chdays_text(m: Message, state: FSMContext):
    raw = m.text.strip()
    if not raw.isdigit() or not (1 <= int(raw) <= 3650):
        return await m.answer("❌ Send a whole number of days between 1 and 3650.", reply_markup=kb.CANCEL_KB)
    await finalize_channel(m, state, int(raw))


async def finalize_channel(ev, state: FSMContext, days: int) -> None:
    d = await state.get_data()
    res = d.get("pending")
    if not res:
        await state.clear()
        return await show_list(ev, 1)
    uid = ev.from_user.id
    rec = {**res, "expires_at": now() + days * 86400, "days_total": days}
    cid = await store.add_channel(rec, uid)
    await state.clear()
    store.log_bg("bot", uid, role_of(uid), "channel_added", f"{cid} {res.get('title')} ({days}d)", cid)
    c = await store.get_channel(cid)
    await ui.show(ev, f"✅ <b>Channel added!</b>\n\n" + await channel_card(c), kb.channel_panel_kb(cid))


# ───────────── renew (extends the channel's own overall validity) ─────────────
@router.callback_query(F.data.startswith("rn:"))
async def renew_cb(cb: CallbackQuery):
    cid = cb.data.split(":", 1)[1]
    await cb.answer()
    rows, row = [], []
    for d in PRESET_DAYS:
        row.append((f"{d} days", f"rnd:{cid}:{d}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([("✏️ Custom", f"rnd:{cid}:custom")])
    rows.append([("⬅️ Back", f"ch:open:{cid}")])
    await ui.show(cb, "🔁 <b>Renew Channel</b>\n\nHow many extra days?", kb.ik(*rows))


@router.callback_query(F.data.startswith("rnd:"))
async def renew_days_cb(cb: CallbackQuery, state: FSMContext):
    _, cid, val = cb.data.split(":")
    if val == "custom":
        await state.set_state(ChannelSt.renew_days)
        await state.update_data(renew_cid=cid)
        await cb.answer()
        return await ui.show(cb, "✏️ Send the number of extra days:", kb.back_kb(f"ch:open:{cid}"))
    await cb.answer()
    await do_renew(cb, cid, int(val))


@router.message(ChannelSt.renew_days, TextNoCmd())
async def renew_days_text(m: Message, state: FSMContext):
    d = await state.get_data()
    cid = d.get("renew_cid")
    if not cid:
        await state.clear()
        return await show_list(m, 1)
    raw = m.text.strip()
    if not raw.isdigit() or not (1 <= int(raw) <= 3650):
        return await m.answer("❌ Send a whole number of days.", reply_markup=kb.back_kb(f"ch:open:{cid}"))
    await state.clear()
    await do_renew(m, cid, int(raw))


async def do_renew(ev, cid: str, days: int) -> None:
    c = await store.get_channel(cid)
    if not c:
        return await ui.show(ev, "⚠️ Channel not found.", kb.HOME_ONLY_KB)
    base = c["expires_at"] if c.get("expires_at") and c["expires_at"] > now() else now()
    new_exp = base + days * 86400
    uid = ev.from_user.id
    await store.update_channel(cid, {"expires_at": new_exp, "days_total": (c.get("days_total") or 0) + days})
    store.log_bg("bot", uid, role_of(uid), "channel_renewed", f"+{days}d", cid)
    c = await store.get_channel(cid)
    await ui.show(ev, (f"✅ <b>RENEWAL SUCCESSFUL!</b>\n\n📺 Channel: {esc(c['title'])}\n📅 Extended by: "
                       f"<b>{days} days</b>\n⏳ {fmt_days_left(new_exp)}"),
                 kb.ik([("⬅️ Control Panel", f"ch:open:{cid}")]))


# ───────────── revoke all / remove ─────────────
@router.callback_query(F.data.startswith("rva:"))
async def revoke_all_cb(cb: CallbackQuery):
    cid = cb.data.split(":", 1)[1]
    await cb.answer()
    await ui.show(cb, "🧨 <b>Revoke ALL services</b> on this channel?\n\nThis disables every active plan. "
                      "Nothing is deleted — you can re-enable any service again any time.",
                 kb.confirm_kb(f"rvago:{cid}", f"ch:open:{cid}"))


@router.callback_query(F.data.startswith("rvago:"))
async def revoke_all_go(cb: CallbackQuery):
    cid = cb.data.split(":", 1)[1]
    uid = cb.from_user.id
    done = await store.revoke_all_services(cid, uid)
    store.log_bg("bot", uid, role_of(uid), "revoke_all", ",".join(done) or "none", cid)
    await cb.answer("Done")
    await ui.show(cb, f"🧨 Revoked: {', '.join(SERVICES[s]['title'] for s in done) or 'nothing was active'}.",
                 kb.ik([("⬅️ Control Panel", f"ch:open:{cid}")]))


@router.callback_query(F.data.startswith("chdel:"))
async def chdel_cb(cb: CallbackQuery):
    cid = cb.data.split(":", 1)[1]
    await cb.answer()
    await ui.show(cb, "🗑 <b>Remove this channel?</b>\n\nIt will disappear from your Channels list and "
                      "all its services will stop. Nothing is actually deleted from the database — ask "
                      "the owner to restore it if needed.", kb.confirm_kb(f"chdelgo:{cid}", f"ch:open:{cid}"))


@router.callback_query(F.data.startswith("chdelgo:"))
async def chdel_go(cb: CallbackQuery):
    cid = cb.data.split(":", 1)[1]
    uid = cb.from_user.id
    await store.revoke_all_services(cid, uid)
    await store.hide_channel(cid, uid)
    store.log_bg("bot", uid, role_of(uid), "channel_removed", "", cid)
    await cb.answer("Removed")
    await show_list(cb, 1)


# ───────────── Stats & Info ─────────────
@router.callback_query(F.data.startswith("st:"))
async def stats_cb(cb: CallbackQuery):
    cid = cb.data.split(":", 1)[1]
    await cb.answer()
    c = await store.get_channel(cid)
    if not c:
        return await ui.show(cb, "⚠️ Channel not found.", kb.HOME_ONLY_KB)
    svcs = await store.get_all_services(cid)
    from .. import db
    from ..engine.campaigns import _chan_key
    base = _chan_key(c)
    subs = len(await db.keys(f"targets/{base}/subs"))
    posts = await db.get(f"posts/{cid}") or {}
    lines = [f"📈 <b>Stats & Info</b>", "", f"📛 {esc(c['title'])}", f"👥 Members (approx, at add time): {c.get('members') or '—'}",
             f"🙋 Subscribed by our sessions: <b>{subs}</b>", f"📰 Posts tracked (last 3 days): {len(posts)}", ""]
    for s in SERVICE_ORDER:
        sv = svcs[s]
        if not sv["enabled"]:
            lines.append(f"{SERVICES[s]['emoji']} {SERVICES[s]['title']}: <i>off</i>")
            continue
        lines.append(f"{SERVICES[s]['emoji']} {SERVICES[s]['title']}: 🟢 {fmt_days_left(sv['expires_at'])}")
    await ui.show(cb, "\n".join(lines), kb.ik([("⬅️ Back", f"ch:open:{cid}")]))
