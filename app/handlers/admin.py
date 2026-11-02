"""Admin-only commands + payment review. Every handler here is restricted to ADMIN_IDS."""
from __future__ import annotations

import logging
from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import db
from .. import keyboards as kb
from ..config import cfg
from ..filters import NotCmd, TextNoCmd
from ..services import broadcast, forcejoin, notify, store
from ..states import AdminSt
from ..utils import esc, money, now, spawn

log = logging.getLogger("admin")
router = Router(name="admin")
_admins = set(cfg.admin_ids)
router.message.filter(F.from_user.id.in_(_admins))
router.callback_query.filter(F.from_user.id.in_(_admins))

HELP = (
    "🛠 <b>Admin Commands</b>\n\n"
    "/ban <code>chatid</code> — ban a user\n/unban <code>chatid</code> — unban\n"
    "/broadcast — message/photo/video/file to all users\n/send <code>chatid</code> — send content to one user\n"
    "/setupi <code>upi@bank</code> — change UPI ID\n/setqr — upload payment QR\n"
    "/setsupport <code>@username</code> — support contact\n"
    "/setforce <code>@channel | -100id | off</code> — force-join channel\n"
    "/sessions — session health summary"
)


def _arg_int(command: CommandObject) -> int | None:
    a = (command.args or "").strip().split()
    return int(a[0]) if a and a[0].lstrip("-").isdigit() else None


@router.message(Command("admin", "help"))
async def admin_help(m: Message):
    await m.answer(HELP)


# ───────────── ban / unban ─────────────
@router.message(Command("ban"))
async def ban(m: Message, command: CommandObject, bot: Bot):
    uid = _arg_int(command)
    if uid is None:
        return await m.answer("Usage: /ban <code>chatid</code>")
    if uid in _admins:
        return await m.answer("❌ You can't ban an admin.")
    await store.set_banned(uid, True)
    store.log_bg("admin", m.from_user.id, "ban", str(uid))
    await m.answer(f"🚫 User <code>{uid}</code> banned.")
    await notify.notify_user(bot, uid, "🚫 You have been banned from using this bot.")


@router.message(Command("unban"))
async def unban(m: Message, command: CommandObject, bot: Bot):
    uid = _arg_int(command)
    if uid is None:
        return await m.answer("Usage: /unban <code>chatid</code>")
    await store.set_banned(uid, False)
    store.log_bg("admin", m.from_user.id, "unban", str(uid))
    await m.answer(f"✅ User <code>{uid}</code> unbanned.")
    await notify.notify_user(bot, uid, "✅ You have been unbanned. Send /start to continue.")


# ───────────── broadcast ─────────────
@router.message(Command("broadcast"))
async def broadcast_cmd(m: Message, state: FSMContext):
    await state.set_state(AdminSt.broadcast)
    await m.answer("📣 <b>Broadcast</b>\n\nSend the message to broadcast — text, image, video or file.\n/cancel to abort.")


@router.message(AdminSt.broadcast, NotCmd())
async def broadcast_content(m: Message, state: FSMContext, bot: Bot):
    kind, preview = broadcast.describe(m)
    await state.update_data(bc_chat=m.chat.id, bc_msg=m.message_id, bc_kind=kind, bc_prev=preview)
    total = len(await db.keys("users"))
    await bot.copy_message(m.chat.id, m.chat.id, m.message_id)  # preview exactly what users will get
    await m.answer(f"👆 This is the preview.\nSend to <b>{total:,}</b> users?", reply_markup=kb.broadcast_kb())


@router.callback_query(F.data == "bc:cancel")
async def bc_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer("Cancelled")
    await cb.message.edit_text("❌ Broadcast cancelled.")


@router.callback_query(F.data == "bc:go")
async def bc_go(cb: CallbackQuery, state: FSMContext, bot: Bot):
    d = await state.get_data()
    if "bc_msg" not in d:
        return await cb.answer("Expired, run /broadcast again.", show_alert=True)
    await state.clear()
    bid = await broadcast.create(bot, cb.from_user.id, d["bc_chat"], d["bc_msg"], d["bc_kind"], d["bc_prev"])
    await cb.answer("🚀 Started")
    await cb.message.edit_text(f"🚀 Broadcast <b>#{int(bid)}</b> started. I'll report when it finishes.")


# ───────────── /send ─────────────
@router.message(Command("send"))
async def send_cmd(m: Message, command: CommandObject, state: FSMContext):
    uid = _arg_int(command)
    if uid is None:
        return await m.answer("Usage: /send <code>chatid</code>")
    await state.set_state(AdminSt.send)
    await state.update_data(send_to=uid)
    await m.answer(f"✉️ Send the message / image / file / video for <code>{uid}</code>.\n/cancel to abort.")


@router.message(AdminSt.send, NotCmd())
async def send_content(m: Message, state: FSMContext, bot: Bot):
    uid = (await state.get_data()).get("send_to")
    await state.clear()
    try:
        await bot.copy_message(uid, m.chat.id, m.message_id)
        store.log_bg("admin", m.from_user.id, "send", str(uid))
        await m.answer(f"✅ Sent to <code>{uid}</code>.")
    except TelegramForbiddenError:
        await m.answer("🚫 That user has blocked the bot.")
    except Exception as e:  # noqa: BLE001
        await m.answer(f"❌ Failed: {esc(e)}")


# ───────────── payment review ─────────────
async def _edit_review(bot: Bot, chat_id: int, message_id: int, html: str, is_photo: bool) -> None:
    with suppress(Exception):
        if is_photo:
            await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=html, reply_markup=None)
        else:
            await bot.edit_message_text(html, chat_id=chat_id, message_id=message_id, reply_markup=None)


@router.callback_query(F.data.startswith("dep:ok:"))
async def dep_ok(cb: CallbackQuery, bot: Bot):
    did = cb.data.split(":")[2]
    try:
        dep, bal = await store.resolve_deposit(did, True, cb.from_user.id)
    except store.AlreadyProcessed:
        return await cb.answer("Already processed by another admin.", show_alert=True)
    except Exception:  # noqa: BLE001
        log.exception("approve failed")
        return await cb.answer("⚠️ Error — nothing was changed. Try again.", show_alert=True)
    await cb.answer("✅ Approved")
    store.log_bg("admin", cb.from_user.id, "deposit_approved", f"#{did} {dep['amount']}")
    await _edit_review(bot, cb.message.chat.id, cb.message.message_id,
                       f"{cb.message.html_text}\n\n✅ <b>Approved</b> by {esc(cb.from_user.full_name)}", bool(cb.message.photo))
    await notify.notify_user(bot, dep["user_id"], (
        f"✅ <b>Payment Approved!</b>\n\n💰 {money(dep['amount'])} has been added to your wallet.\n"
        f"👛 New balance: <b>{money(bal)}</b>\n\nReady to grow your channel? 🚀"), kb.HOME_KB)


@router.callback_query(F.data.startswith("dep:no:"))
async def dep_no(cb: CallbackQuery, state: FSMContext):
    did = cb.data.split(":")[2]
    dep = await store.get_deposit(did)
    if not dep or dep["status"] != "pending":
        return await cb.answer("Already processed.", show_alert=True)
    await cb.answer()
    await state.set_state(AdminSt.reject_reason)
    await state.update_data(did=did, chat_id=cb.message.chat.id, message_id=cb.message.message_id,
                            html=cb.message.html_text, is_photo=bool(cb.message.photo))
    await cb.message.answer(f"✍️ Send the <b>reason</b> for rejecting request <b>#{int(did)}</b> "
                            f"({money(dep['amount'])}).\n/cancel to abort.")


@router.message(AdminSt.reject_reason, TextNoCmd())
async def reject_reason(m: Message, state: FSMContext, bot: Bot):
    d = await state.get_data()
    reason = m.text.strip()[:300]
    await state.clear()
    try:
        dep, _ = await store.resolve_deposit(d["did"], False, m.from_user.id, reason)
    except store.AlreadyProcessed:
        return await m.answer("Already processed by another admin.")
    store.log_bg("admin", m.from_user.id, "deposit_rejected", f"#{d['did']} {reason}")
    await _edit_review(bot, d["chat_id"], d["message_id"],
                       f"{d['html']}\n\n❌ <b>Rejected</b> by {esc(m.from_user.full_name)}\n📝 {esc(reason)}", d["is_photo"])
    await m.answer("❌ Rejected. The user has been notified.")
    await notify.notify_user(bot, dep["user_id"], (
        f"❌ <b>Payment Failed</b>\n\nYour deposit of <b>{money(dep['amount'])}</b> (request #{int(dep['id'])}) "
        f"could not be verified.\n📝 Reason: {esc(reason)}\n\nIf you think this is a mistake, please contact support."),
        kb.HOME_KB)


# ───────────── settings (UI comes in the admin panel, Phase 4) ─────────────
@router.message(Command("setupi"))
async def setupi(m: Message, command: CommandObject):
    upi = (command.args or "").strip()
    if "@" not in upi:
        return await m.answer("Usage: /setupi <code>name@bank</code>")
    await store.set_setting("upi_id", upi)
    store.log_bg("admin", m.from_user.id, "set_upi", upi)
    await m.answer(f"✅ UPI ID set to <code>{esc(upi)}</code>")


@router.message(Command("setsupport"))
async def setsupport(m: Message, command: CommandObject):
    u = (command.args or "").strip().lstrip("@")
    if not u:
        return await m.answer("Usage: /setsupport <code>@username</code>")
    await store.set_setting("support_username", u)
    await m.answer(f"✅ Support contact set to @{esc(u)}")


@router.message(Command("setqr"))
async def setqr(m: Message, state: FSMContext):
    await state.set_state(AdminSt.qr)
    await m.answer("📷 Send the payment <b>QR code image</b> (as a photo).\n/cancel to abort.")


@router.message(AdminSt.qr, F.photo)
async def got_qr(m: Message, state: FSMContext):
    await store.set_setting("qr_file_id", m.photo[-1].file_id)  # stored inside Telegram, only the file_id is kept
    await state.clear()
    store.log_bg("admin", m.from_user.id, "set_qr")
    await m.answer("✅ QR code saved. Users will see it on the deposit screen.")


@router.message(AdminSt.qr, NotCmd())
async def qr_not_photo(m: Message):
    await m.answer("📷 Please send the QR as a photo.")


@router.message(Command("setforce"))
async def setforce(m: Message, command: CommandObject, bot: Bot):
    ok, msg = await forcejoin.set_force_channel(bot, command.args)
    store.log_bg("admin", m.from_user.id, "set_force", command.args or "off")
    await m.answer(msg if ok else f"❌ {msg}")


@router.message(Command("sessions"))
async def sessions_cmd(m: Message):
    from .. import runtime
    c = runtime.engine.sessions.counts() if runtime.engine else {}
    await m.answer("🧩 <b>Sessions</b>\n\n" + "\n".join(f"• {k}: <b>{v}</b>" for k, v in c.items())
                   + "\n\nManage them in the admin panel.")
