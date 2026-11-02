from __future__ import annotations

import asyncio
from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from .. import ui
from ..config import cfg
from ..constants import (DEPOSIT_STATUS, MAX_DEPOSIT, MAX_PENDING_DEPOSITS, MIN_DEPOSIT, ORDER_STATUS, PAGE_SIZE,
                         SERVICES)
from ..filters import TextNoCmd
from ..services import notify, store
from ..states import DepositSt
from ..utils import esc, fmt_ts, money, now, paginate
from . import common

router = Router(name="wallet")


# ───────────── wallet ─────────────
async def show_wallet(ev) -> None:
    uid = ev.from_user.id
    bal, pend = await asyncio.gather(store.get_balance(uid), store.pending_count(uid))
    text = f"💰 <b>My Wallet</b>\n\n💳 Total Balance: <b>{money(bal)}</b>"
    if pend:
        text += f"\n⏳ Deposits awaiting approval: <b>{pend}</b>"
    await ui.show(ev, text, kb.WALLET_KB)


@router.callback_query(F.data == "wallet")
async def wallet_cb(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await show_wallet(cb)


# ───────────── histories (10 per page) ─────────────
async def show_deposit_history(ev, page: int) -> None:
    ids = await store.user_deposit_ids(ev.from_user.id)
    back = [("💰 Wallet", "wallet"), ("🏠 Home", "home")]
    if not ids:
        return await ui.show(ev, "🧾 <b>Deposit History</b>\n\nNo deposits yet.", kb.ik([("➕ Deposit", "dep:start")], back))
    chunk, page, pages = paginate(ids, page, PAGE_SIZE)
    lines = ["🧾 <b>Deposit History</b>\n"]
    for d in await store.get_deposits(chunk):
        dot, label = DEPOSIT_STATUS.get(d["status"], ("⚪", d["status"]))
        extra = f"\n      📝 {esc(d['reason'])}" if d.get("reason") else ""
        lines.append(f"{dot} <b>{money(d['amount'])}</b> · {label}\n      🕒 {fmt_ts(d['created_at'], cfg.timezone)}{extra}")
    await ui.show(ev, "\n".join(lines), kb.pager_kb("dh", page, pages, back))


async def show_history(ev, page: int) -> None:
    ids = await store.user_order_ids(ev.from_user.id)
    if not ids:
        return await ui.show(ev, "📜 <b>Order History</b>\n\nYou haven't bought anything yet. Let's change that! 🚀", kb.HOME_KB)
    chunk, page, pages = paginate(ids, page, PAGE_SIZE)
    lines = ["📜 <b>Order History</b>\n"]
    for o in await store.get_orders(chunk):
        emoji, label = ORDER_STATUS.get(o["status"], ("❔", o["status"]))
        s = SERVICES[o["service"]]
        lines.append(f"{emoji} <b>#{int(o['id'])}</b> · {s['emoji']} {int(o['qty']):,} {s['units']} · {money(o['price'])}\n"
                     f"      {label} · {fmt_ts(o['created_at'], cfg.timezone)}")
    await ui.show(ev, "\n".join(lines), kb.pager_kb("oh", page, pages))


@router.callback_query(F.data.startswith("dh:"))
async def dh_cb(cb: CallbackQuery):
    await cb.answer()
    await show_deposit_history(cb, int(cb.data.split(":")[1]))


@router.callback_query(F.data.startswith("oh:"))
async def oh_cb(cb: CallbackQuery):
    await cb.answer()
    await show_history(cb, int(cb.data.split(":")[1]))


# ───────────── deposit ─────────────
@router.callback_query(F.data == "dep:start")
async def dep_start(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await ui.show(cb, f"➕ <b>Add Money</b>\n\nChoose an amount 👇\n(minimum {money(MIN_DEPOSIT)})", kb.DEPOSIT_AMOUNT_KB)


@router.callback_query(F.data.startswith("dep:amt:"))
async def dep_amt(cb: CallbackQuery, state: FSMContext, bot: Bot):
    amount = int(cb.data.split(":")[2])
    if not MIN_DEPOSIT <= amount <= MAX_DEPOSIT:
        return await cb.answer()
    await cb.answer()
    await send_payment(cb, state, bot, amount)


@router.callback_query(F.data == "dep:custom")
async def dep_custom(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(DepositSt.amount)
    await ui.show(cb, f"✏️ <b>Custom Amount</b>\n\nSend the amount in rupees.\nMin {money(MIN_DEPOSIT)} — Max {money(MAX_DEPOSIT)}",
                  kb.CANCEL_KB)


@router.message(DepositSt.amount, TextNoCmd())
async def dep_amount_text(m: Message, state: FSMContext, bot: Bot):
    raw = m.text.strip().replace(",", "").lstrip("₹").strip()
    if not raw.isdigit() or not MIN_DEPOSIT <= int(raw) <= MAX_DEPOSIT:
        return await m.answer(f"❌ Enter a whole number between {money(MIN_DEPOSIT)} and {money(MAX_DEPOSIT)}.",
                              reply_markup=kb.CANCEL_KB)
    await send_payment(m, state, bot, int(raw))


async def send_payment(ev, state: FSMContext, bot: Bot, amount: int) -> None:
    uid = ev.from_user.id
    if await store.pending_count(uid) >= MAX_PENDING_DEPOSITS:
        await state.clear()
        return await ui.show(ev, f"⏳ You already have {MAX_PENDING_DEPOSITS} deposits waiting for approval.\n"
                                 f"Please wait until they're verified.", kb.HOME_ONLY_KB)
    s = await store.get_settings()
    upi, qr = s.get("upi_id") or cfg.upi_id, s.get("qr_file_id")
    await state.set_state(None)
    await state.update_data(dep_amount=amount)
    caption = (f"💳 <b>Add {money(amount)}</b>\n\n1️⃣ Scan the QR code or pay to UPI ID:\n"
               f"<code>{esc(upi) if upi else 'not configured'}</code>  <i>(tap to copy)</i>\n\n"
               f"2️⃣ Pay exactly <b>{money(amount)}</b>\n3️⃣ Tap <b>Submit Payment Screenshot</b> and upload the proof\n\n"
               f"⏳ Manual verification takes <b>2–3 hours</b>. Please be patient 🙏")
    if qr:
        msg = ui.msg_of(ev)
        if isinstance(ev, CallbackQuery):
            with suppress(Exception):
                await msg.delete()
        try:
            return await bot.send_photo(msg.chat.id, qr, caption=caption, reply_markup=kb.DEPOSIT_PAY_KB)
        except Exception:  # noqa: BLE001  bad file_id -> fall back to text
            pass
    await ui.show(ev, caption, kb.DEPOSIT_PAY_KB)


@router.callback_query(F.data == "dep:submit")
async def dep_submit(cb: CallbackQuery, state: FSMContext):
    if not (await state.get_data()).get("dep_amount"):
        await cb.answer("⏳ Session expired. Please start the deposit again.", show_alert=True)
        return await common.show_home(cb, state)
    await cb.answer()
    await state.set_state(DepositSt.screenshot)
    await ui.show(cb, "📸 <b>Upload Payment Screenshot</b>\n\nSend the screenshot of your successful payment "
                      "(as a photo).", kb.CANCEL_KB)


@router.message(DepositSt.screenshot, F.photo)
async def got_screenshot(m: Message, state: FSMContext, bot: Bot):
    amount = (await state.get_data()).get("dep_amount")
    if not amount:
        await state.clear()
        return await m.answer("⏳ Session expired. Please start the deposit again.", reply_markup=kb.HOME_ONLY_KB)
    proof = m.photo[-1].file_id  # only the Telegram file_id is stored
    key = await store.create_deposit(m.from_user, amount, proof)
    await state.clear()
    store.log_bg("bot", m.from_user.id, "deposit_submitted", f"#{key} {amount}")
    await m.answer(f"✅ <b>Payment proof submitted!</b>\n\n🧾 Request: <b>#{int(key)}</b>\n💰 Amount: <b>{money(amount)}</b>\n\n"
                   f"⏳ Manual verification takes <b>2–3 hours</b>. We'll notify you the moment it's approved. Please be patient 🙏",
                   reply_markup=kb.HOME_ONLY_KB)
    u = m.from_user
    await notify.notify_admins(bot, (
        f"💳 <b>Payment Proof Submitted</b> — please verify\n\n🧾 Request: <b>#{int(key)}</b>\n👤 Name: {esc(u.full_name)}\n"
        f"🆔 Chat ID: <code>{u.id}</code>\n🔗 Username: {'@' + esc(u.username) if u.username else '—'}\n"
        f"💰 Amount: <b>{money(amount)}</b>\n🕒 {fmt_ts(now(), cfg.timezone)}"),
        photo=proof, markup=kb.admin_deposit_kb(key))


@router.message(DepositSt.screenshot)
async def not_photo(m: Message):
    await m.answer("📸 Please send the payment screenshot as a <b>photo</b>.", reply_markup=kb.CANCEL_KB)
