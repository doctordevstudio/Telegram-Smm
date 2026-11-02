"""Order flows for all 4 services: choose target -> plan/custom qty -> (split / reactions) -> pay from wallet."""
from __future__ import annotations

import asyncio
import re
from collections import defaultdict

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, MessageOriginChannel

from .. import keyboards as kb
from .. import ui
from .. import runtime
from ..config import cfg
from ..constants import SERVICES
from ..filters import TextNoCmd
from ..services import availability, notify, resolver, store
from ..states import OrderSt
from ..utils import (calc_price, esc, fmt_reactions, fmt_splits, fmt_ts, money, now, parse_reactions, parse_splits,
                     parse_tg_link, spawn)
from . import common

router = Router(name="order")
_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)  # one payment at a time per user


# ───────────── helpers ─────────────
def _title(svc: str) -> str:
    s = SERVICES[svc]
    return f"{s['emoji']} {s['title']}"


def _head(svc: str, t: dict) -> list[str]:
    lines = [f"{SERVICES[svc]['emoji']} <b>Get {SERVICES[svc]['title']}</b>", "",
             f"📺 <b>{esc(t.get('title') or 'Channel')}</b>", f"🔗 {esc(t.get('link', ''))}"]
    if svc in ("views", "reactions"):
        lines.append(f"📨 Post ID: <code>{t['message_id']}</code>")
    if svc == "stream" and t.get("stream"):
        lines.append("🎥 Stream invite link detected")
    return lines


async def _expired(ev, state: FSMContext) -> None:
    if isinstance(ev, CallbackQuery):
        await ev.answer("⏳ This session expired. Please start again.", show_alert=True)
    await common.show_home(ev, state)


def _target_from_channel(ch: dict) -> dict:
    return {"chat_id": ch.get("chat_id"), "username": ch.get("username"), "invite_hash": ch.get("invite_hash"),
            "link": ch["link"], "title": ch.get("title"), "allowed": ch.get("reactions_allowed")}


def _same_channel(ch: dict, chat_id: int | None, username: str | None) -> bool:
    if ch.get("chat_id") and chat_id and ch["chat_id"] == chat_id:
        return True
    if ch.get("username") and username and ch["username"].lower() == username.lower():
        return True
    # private channel whose numeric ID is still unknown: bind it to the first post the user shows us
    # (Phase 2 re-verifies via the sessions that join with the invite link)
    return ch.get("type") == "private" and not ch.get("chat_id") and not username


# ───────────── entry: tap a service ─────────────
@router.callback_query(F.data.startswith("svc:"))
async def on_service(cb: CallbackQuery, state: FSMContext):
    svc = cb.data.split(":", 1)[1]
    if svc not in SERVICES:
        return await cb.answer()
    await cb.answer()
    await state.clear()
    await state.update_data(svc=svc)
    if svc == "subscribers":
        ch = await store.get_channel(cb.from_user.id)
        if not ch:
            return await common.ask_channel_link(cb, state, "👋 First, let's add your channel!\n\n")
        await state.update_data(target=_target_from_channel(ch))
        return await show_plans(cb, state)
    if svc in ("views", "reactions"):
        await state.set_state(OrderSt.post)
        text = (f"{SERVICES[svc]['emoji']} <b>Get {SERVICES[svc]['title']}</b>\n\n"
                "📨 <b>Forward a post</b> from your channel here,\nor paste the <b>post link</b> "
                "(e.g. <code>https://t.me/channel/123</code>).\n\n"
                "💡 Groups or channels with forwarding disabled? Use the post link.")
    else:
        await state.set_state(OrderSt.stream)
        text = ("🔴 <b>Get Live Stream Viewers</b>\n\nSend the <b>channel link</b> or the <b>live stream invite link</b>:\n\n"
                "<code>https://t.me/channel</code>\n<code>https://t.me/channel?livestream=…</code>\n"
                "<code>https://t.me/+AbCd…</code> (private)")
    await ui.show(cb, text, kb.CANCEL_KB)


# ───────────── target: forwarded post / link / stream link ─────────────
@router.message(OrderSt.post)
async def got_post(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    if d.get("svc") not in ("views", "reactions"):
        return await _expired(m, state)
    chat_id = username = title = None
    mid = None
    origin = m.forward_origin
    if isinstance(origin, MessageOriginChannel):
        chat_id, mid = origin.chat.id, origin.message_id
        title, username = origin.chat.title, origin.chat.username
    elif m.text:
        link = parse_tg_link(m.text)
        if link and link.message_id and link.kind in ("public", "private_post"):
            mid = link.message_id
            if link.kind == "public":
                chat = await resolver.get_chat(bot, f"@{link.username}")
                if chat is None:
                    return await m.answer(resolver.ERR_NOT_FOUND, reply_markup=kb.CANCEL_KB)
                chat_id, title, username = chat.id, chat.title, chat.username
            else:
                chat_id = link.chat_id
    if mid is None:
        return await m.answer("❌ I couldn't detect a channel post.\n\n📨 Forward a post from the channel, or paste "
                              "the post link (<code>https://t.me/channel/123</code>).", reply_markup=kb.CANCEL_KB)

    ch = await store.get_channel(m.from_user.id)
    if ch and _same_channel(ch, chat_id, username):
        target = {**_target_from_channel(ch), "chat_id": chat_id or ch.get("chat_id")}
        if not ch.get("chat_id") and chat_id:
            await store.save_channel(m.from_user.id, {**ch, "chat_id": chat_id})
    elif username:  # any public channel is fine: sessions can join it by username
        target = {"chat_id": chat_id, "username": username, "link": f"https://t.me/{username}", "title": title}
    else:
        return await m.answer("🔒 That post is from a <b>private channel</b> that isn't added to your account.\n\n"
                              "Add it first via <b>📺 My Channel</b> (sessions need its invite link to join).",
                              reply_markup=kb.CANCEL_KB)
    target["message_id"] = mid
    await state.update_data(target=target)
    await show_plans(m, state)


@router.message(OrderSt.stream, TextNoCmd())
async def got_stream(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    if d.get("svc") != "stream":
        return await _expired(m, state)
    link = parse_tg_link(m.text)
    if not link or link.kind == "private_post":
        return await m.answer("❌ Please send a channel link or a live-stream invite link.", reply_markup=kb.CANCEL_KB)
    if link.kind == "public":
        chat = await resolver.get_chat(bot, f"@{link.username}")
        if chat is None:
            return await m.answer(resolver.ERR_NOT_FOUND, reply_markup=kb.CANCEL_KB)
        target = {"chat_id": chat.id, "username": chat.username, "link": link.url, "title": chat.title}
    else:
        target = {"chat_id": None, "invite_hash": link.invite_hash, "link": link.url, "title": "Private Channel"}
        ch = await store.get_channel(m.from_user.id)
        if ch and ch.get("invite_hash") == link.invite_hash:
            target.update(chat_id=ch.get("chat_id"), title=ch.get("title"))
    if link.stream is not None:
        target["stream"] = link.stream or "live"
        target["stream_url"] = m.text.strip().split()[0]
    await state.update_data(target=target)
    await show_plans(m, state)


# ───────────── plans ─────────────
async def show_plans(ev, state: FSMContext) -> None:
    d = await state.get_data()
    svc, t = d["svc"], d["target"]
    plans, pr, (avail, total) = await asyncio.gather(
        store.get_plans(svc), store.get_pricing(svc), availability.get_available(svc, t))
    await state.set_state(None)
    await state.update_data(avail=avail)
    s = SERVICES[svc]
    label = "Remaining reactions on this post" if svc == "reactions" else f"Available {s['units']}"
    lines = _head(svc, t) + ["", f"✅ {label}: <b>{avail:,}</b> / {total:,}",
                             f"💵 Rate: <b>{money(pr['unit'])}</b> per {s['unit']}", ""]
    if avail < pr["min"]:
        lines.append("⚠️ Not enough capacity right now. Please try again a little later.")
        return await ui.show(ev, "\n".join(lines), kb.ik([("🏠 Home", "home")]))
    shown = [p for p in plans if p["qty"] <= avail]
    lines.append("Choose a plan 👇")
    await ui.show(ev, "\n".join(lines), kb.plans_kb(shown, svc))


@router.callback_query(F.data.startswith("plan:"))
async def on_plan(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if "svc" not in d or "target" not in d:
        return await _expired(cb, state)
    plan = await store.get_plan(d["svc"], cb.data.split(":", 1)[1])
    if not plan:
        return await cb.answer("This plan is no longer available.", show_alert=True)
    if plan["qty"] > d.get("avail", 0):
        return await cb.answer("Not enough capacity for this plan right now.", show_alert=True)
    await cb.answer()
    await state.update_data(qty=plan["qty"], price=plan["price"], plan_id=plan["id"])
    await after_qty(cb, state)


@router.callback_query(F.data == "custom")
async def on_custom(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if "svc" not in d or "target" not in d:
        return await _expired(cb, state)
    await cb.answer()
    pr = await store.get_pricing(d["svc"])
    hi = min(pr["max"], d.get("avail", 0))
    await state.set_state(OrderSt.qty)
    s = SERVICES[d["svc"]]
    await ui.show(cb, f"✏️ <b>Custom Quantity</b>\n\nSend how many {s['units']} you want:\n"
                      f"🔢 Min <b>{pr['min']:,}</b> — Max <b>{hi:,}</b>\n💵 Rate: {money(pr['unit'])} per {s['unit']}",
                  kb.CANCEL_KB)


@router.message(OrderSt.qty, TextNoCmd())
async def got_qty(m: Message, state: FSMContext):
    d = await state.get_data()
    if "svc" not in d or "target" not in d:
        return await _expired(m, state)
    raw = re.sub(r"[,\s]", "", m.text)
    pr = await store.get_pricing(d["svc"])
    hi = min(pr["max"], d.get("avail", 0))
    if not raw.isdigit() or not (pr["min"] <= int(raw) <= hi):
        return await m.answer(f"❌ Please send a number between <b>{pr['min']:,}</b> and <b>{hi:,}</b>.",
                              reply_markup=kb.CANCEL_KB)
    qty = int(raw)
    await state.update_data(qty=qty, price=calc_price(qty, pr["unit"]), plan_id=None)
    await after_qty(m, state)


async def after_qty(ev, state: FSMContext) -> None:
    svc = (await state.get_data())["svc"]
    if svc == "views":
        return await ask_split(ev, state)
    if svc == "reactions":
        return await ask_reactions(ev, state)
    await show_summary(ev, state)


# ───────────── views: split ─────────────
async def ask_split(ev, state: FSMContext) -> None:
    qty = (await state.get_data())["qty"]
    await state.set_state(OrderSt.split)
    text = (f"⏱ <b>Delivery Speed</b>\n\n<b>{qty:,}</b> views — how fast should they arrive?\n"
            f"Pick a preset 👇 or send your own <b>split</b>:\n\n"
            f"<code>200 in 30</code>  → 200 views in 30 min\n<code>{max(qty - 200, 1)} in 120</code>  → next batch over 120 min\n\n"
            f"📌 Splits run one after another. Total must equal <b>{qty:,}</b> views and "
            f"max <b>1440 min</b> (24h).")
    await ui.show(ev, text, kb.SPLIT_KB)


@router.callback_query(F.data.startswith("split:"))
async def on_split(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if d.get("svc") != "views" or "qty" not in d:
        return await _expired(cb, state)
    await cb.answer()
    await state.update_data(splits=[[d["qty"], int(cb.data.split(":")[1])]])
    await show_summary(cb, state)


@router.message(OrderSt.split, TextNoCmd())
async def got_split(m: Message, state: FSMContext):
    d = await state.get_data()
    if d.get("svc") != "views" or "qty" not in d:
        return await _expired(m, state)
    splits, err = parse_splits(m.text, d["qty"])
    if err:
        return await m.answer(f"❌ {err}", reply_markup=kb.SPLIT_KB)
    await state.update_data(splits=splits)
    await show_summary(m, state)


# ───────────── reactions: configure ─────────────
async def ask_reactions(ev, state: FSMContext) -> None:
    qty = (await state.get_data())["qty"]
    await state.set_state(OrderSt.reactions)
    text = (f"❤️ <b>Configure Reactions</b>\n\nYou chose <b>{qty:,}</b> reactions. Send how many of each emoji:\n\n"
            f"<code>❤️15 😍20</code>\n\n📌 Numbers must add up to exactly <b>{qty:,}</b>.")
    await ui.show(ev, text, kb.CANCEL_KB)


@router.message(OrderSt.reactions, TextNoCmd())
async def got_reactions(m: Message, state: FSMContext):
    d = await state.get_data()
    if d.get("svc") != "reactions" or "qty" not in d:
        return await _expired(m, state)
    items, err = parse_reactions(m.text, d["qty"], (d.get("target") or {}).get("allowed"))
    if err:
        return await m.answer(f"❌ {err}", reply_markup=kb.CANCEL_KB)
    await state.update_data(reactions=items)
    await m.answer(f"💬 <b>Your reaction mix</b>\n\n{fmt_reactions(items)}\n\nTotal: <b>{d['qty']:,}</b>\n\n"
                   f"Continue or edit?", reply_markup=kb.REACT_CONFIRM_KB)


@router.callback_query(F.data == "react:edit")
async def react_edit(cb: CallbackQuery, state: FSMContext):
    if (await state.get_data()).get("svc") != "reactions":
        return await _expired(cb, state)
    await cb.answer()
    await ask_reactions(cb, state)


@router.callback_query(F.data == "react:ok")
async def react_ok(cb: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if d.get("svc") != "reactions" or not d.get("reactions"):
        return await _expired(cb, state)
    await cb.answer()
    await show_summary(cb, state)


# ───────────── summary + pay ─────────────
async def show_summary(ev, state: FSMContext) -> None:
    d = await state.get_data()
    svc, t, qty, price = d["svc"], d["target"], d["qty"], d["price"]
    bal = await store.get_balance(ev.from_user.id)
    await state.set_state(None)
    lines = ["🧾 <b>Order Summary</b>", "", f"🛍 Service: <b>{_title(svc)}</b>"] + _head(svc, t)[2:]
    lines.append(f"🔢 Quantity: <b>{qty:,}</b>")
    if svc == "views":
        lines.append(f"⏱ Delivery: {fmt_splits(d['splits'])}")
    if svc == "reactions":
        lines.append(f"💬 Reactions: {fmt_reactions(d['reactions'])}")
    lines += ["", f"💵 Price: <b>{money(price)}</b>", f"👛 Wallet balance: <b>{money(bal)}</b>", ""]
    can_pay = bal + 1e-9 >= price
    lines.append("Pay from your wallet? 👇" if can_pay
                 else f"⚠️ You're short by <b>{money(price - bal)}</b>. Add money to continue.")
    await ui.show(ev, "\n".join(lines), kb.summary_kb(can_pay, price))


@router.callback_query(F.data == "pay:yes")
async def pay(cb: CallbackQuery, state: FSMContext, bot: Bot):
    uid = cb.from_user.id
    async with _locks[uid]:
        d = await state.get_data()
        if not d.get("svc") or "price" not in d or "target" not in d:
            return await _expired(cb, state)
        svc, t, qty, price = d["svc"], d["target"], int(d["qty"]), float(d["price"])
        avail, _ = await availability.get_available(svc, t)  # capacity may have changed meanwhile
        if qty > avail:
            await cb.answer(f"Only {avail:,} available right now. Please pick a smaller plan.", show_alert=True)
            return await show_plans(cb, state)
        try:
            bal = await store.debit(uid, price)
        except store.InsufficientFunds:
            await cb.answer("❌ Insufficient wallet balance.", show_alert=True)
            return await show_summary(cb, state)
        await state.clear()  # a second tap can never charge twice
        order = {"user_id": uid, "user": {"name": cb.from_user.full_name, "username": cb.from_user.username or ""},
                 "service": svc, "qty": qty, "price": price, "status": "queued", "delivered": 0, "target": t,
                 "created_at": now(), "plan_id": d.get("plan_id") or "custom"}
        if svc == "views":
            order["splits"] = d["splits"]
        if svc == "reactions":
            order["reactions"] = d["reactions"]
        try:
            oid = await store.create_order(uid, order)
        except Exception:  # noqa: BLE001  never keep the money if the order wasn't saved
            await store.credit(uid, price)
            await cb.answer("⚠️ Something went wrong. You were not charged.", show_alert=True)
            raise
    await cb.answer("✅ Payment successful")
    if runtime.engine:
        runtime.engine.runner.kick(oid)  # start instantly instead of waiting for the next scheduler tick
    spawn(store.bump_stats(orders=1, spent=price, **{f"orders_{svc}": 1}))
    store.log_bg("bot", uid, "order_created", f"#{oid} {svc} x{qty} {price}")
    num = int(oid)
    await ui.show(cb, (f"✅ <b>Transaction Successful!</b>\n\n🧾 Order: <b>#{num}</b>\n🛍 {_title(svc)} × <b>{qty:,}</b>\n"
                       f"💵 Paid: <b>{money(price)}</b>\n👛 Balance: <b>{money(bal)}</b>\n\n"
                       f"🚀 Your order is queued and starts automatically. We'll message you when it's done!"),
                  kb.AFTER_ORDER_KB)
    u = cb.from_user
    spawn(notify.notify_admins(bot, (
        f"🛒 <b>New Order #{num}</b>\n\n👤 {esc(u.full_name)} (<code>{u.id}</code>) "
        f"{'@' + esc(u.username) if u.username else ''}\n🛍 {_title(svc)} × {qty:,}\n💵 {money(price)}\n"
        f"📺 {esc(t.get('title'))} — {esc(t.get('link'))}\n🕒 {fmt_ts(now(), cfg.timezone)}")))
