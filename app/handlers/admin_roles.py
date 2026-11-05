"""Owner-only: grant/revoke bot access to other admins, and set the rotating-residential proxy that
all sessions route through."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from .. import ui
from ..config import cfg
from ..filters import IsOwner
from ..roles import add_admin, is_owner, list_admins_all, list_admins_visible, remove_admin, role_of
from ..services import store
from ..states import AdminMgmtSt, ProxySt
from ..utils import esc, fmt_ts

router = Router(name="admin_roles")
router.message.filter(IsOwner())
router.callback_query.filter(IsOwner())


async def _list_text() -> tuple[str, list[dict]]:
    admins = await list_admins_visible()
    lines = ["👥 <b>Admins</b>", "", f"👑 Owner: <code>{cfg.owner_id}</code>", ""]
    if admins:
        for a in admins:
            lines.append(f"🧑‍💼 {esc(a.get('username') or a['id'])} · <code>{a['id']}</code> · since {fmt_ts(a['added_at'], cfg.timezone)}")
    else:
        lines.append("<i>No extra admins yet.</i>")
    return "\n".join(lines), admins


@router.message(F.text == "👥 Admins")
async def m_admins(m: Message, state: FSMContext):
    await state.clear()
    text, admins = await _list_text()
    await m.answer(text, reply_markup=kb.admins_kb(admins))


@router.callback_query(F.data == "admadd")
async def admadd_cb(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(AdminMgmtSt.add_id)
    await ui.show(cb, "✏️ Send the Telegram chat ID of the person to give bot access to.\n\n"
                      "They can get it from @userinfobot.", kb.CANCEL_KB)


@router.message(AdminMgmtSt.add_id, F.text)
async def admadd_text(m: Message, state: FSMContext, bot: Bot):
    raw = m.text.strip()
    if not raw.lstrip("-").isdigit():
        return await m.answer("❌ Send a numeric chat ID.", reply_markup=kb.CANCEL_KB)
    uid = int(raw)
    await state.clear()
    if is_owner(uid):
        return await m.answer("That's already you (the owner).")
    name, username = "", ""
    try:
        chat = await bot.get_chat(uid)
        name, username = chat.full_name or "", chat.username or ""
    except Exception:  # noqa: BLE001
        pass
    ok = await add_admin(uid, name, username, m.from_user.id)
    if not ok:
        return await m.answer("ℹ️ That user already has access.")
    store.log_bg("bot", m.from_user.id, "owner", "admin_added", str(uid))
    await m.answer(f"✅ <code>{uid}</code> can now use this bot.")
    try:
        await bot.send_message(uid, "👑 You've been granted access to the Telegram SMM control bot. Send /start.")
    except Exception:  # noqa: BLE001
        pass
    text, admins = await _list_text()
    await m.answer(text, reply_markup=kb.admins_kb(admins))


@router.callback_query(F.data.startswith("admrm:"))
async def admrm_cb(cb: CallbackQuery, bot: Bot):
    uid = int(cb.data.split(":", 1)[1])
    ok = await remove_admin(uid, cb.from_user.id)
    if not ok:
        await cb.answer("Already removed.", show_alert=True)
    else:
        store.log_bg("bot", cb.from_user.id, "owner", "admin_removed", str(uid))
        await cb.answer("Removed")
        try:
            await bot.send_message(uid, "🚫 Your access to the Telegram SMM control bot has been revoked.")
        except Exception:  # noqa: BLE001
            pass
    text, admins = await _list_text()
    await ui.show(cb, text, kb.admins_kb(admins))


@router.message(Command("admins"))
async def cmd_admins(m: Message):
    text, admins = await _list_text()
    await m.answer(text, reply_markup=kb.admins_kb(admins))


@router.message(Command("audit_admins"))
async def cmd_audit_admins(m: Message):
    """Owner-only audit trail: shows every admin ever added, including removed ones — nothing is deleted."""
    all_admins = await list_admins_all()
    if not all_admins:
        return await m.answer("No admins have ever been added.")
    lines = ["🕵️ <b>Admin audit (full history, nothing deleted)</b>", ""]
    for a in all_admins:
        state = "🚫 removed" if a.get("hidden") else "🟢 active"
        lines.append(f"{state} · {esc(a.get('username') or a['id'])} · <code>{a['id']}</code> · "
                     f"added {fmt_ts(a['added_at'], cfg.timezone)}")
    await m.answer("\n".join(lines))


# ───────────── proxy (IP Royale rotating residential, or any SOCKS5/HTTP proxy) ─────────────
@router.message(F.text == "🌐 Proxy")
async def m_proxy(m: Message, state: FSMContext):
    await state.clear()
    p = await store.get_proxy()
    text = (f"🌐 <b>Rotating Proxy</b>\n\nAll sessions route their Telegram traffic through this proxy "
           f"when enabled (e.g. an IPRoyal rotating residential endpoint).\n\n"
           f"Status: {'🟢 ENABLED' if p['enabled'] else '⚪ DISABLED'}\n"
           f"URL: <code>{esc(p['url']) if p['url'] else 'not set'}</code>")
    rows = [[("✏️ Set URL", "proxyset")], [(("🔴 Disable" if p["enabled"] else "🟢 Enable"), f"proxytg:{0 if p['enabled'] else 1}")]]
    await m.answer(text, reply_markup=kb.ik(*rows))


@router.callback_query(F.data == "proxyset")
async def proxyset_cb(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(ProxySt.url)
    await ui.show(cb, "✏️ Send the proxy URL, e.g.\n<code>socks5://user:pass@gw.iproyal.com:12345</code>\n"
                      "or <code>http://user:pass@host:port</code>", kb.CANCEL_KB)


@router.message(ProxySt.url, F.text)
async def proxyset_text(m: Message, state: FSMContext):
    url = m.text.strip()
    await state.clear()
    if not url.lower().startswith(("socks5://", "socks4://", "http://")):
        return await m.answer("❌ URL must start with socks5://, socks4:// or http://")
    await store.set_setting("proxy", {"enabled": True, "url": url})
    store.log_bg("bot", m.from_user.id, "owner", "proxy_set", "url updated")
    await m.answer("✅ Proxy saved and enabled. New session connections will use it from now on.")


@router.callback_query(F.data.startswith("proxytg:"))
async def proxytg_cb(cb: CallbackQuery):
    enabled = bool(int(cb.data.split(":")[1]))
    p = await store.get_proxy()
    await store.set_setting("proxy", {"enabled": enabled, "url": p["url"]})
    store.log_bg("bot", cb.from_user.id, "owner", "proxy_toggle", str(enabled))
    await cb.answer("Updated")
    p = await store.get_proxy()
    text = (f"🌐 <b>Rotating Proxy</b>\n\nStatus: {'🟢 ENABLED' if p['enabled'] else '⚪ DISABLED'}\n"
           f"URL: <code>{esc(p['url']) if p['url'] else 'not set'}</code>")
    rows = [[("✏️ Set URL", "proxyset")], [(("🔴 Disable" if p["enabled"] else "🟢 Enable"), f"proxytg:{0 if p['enabled'] else 1}")]]
    await ui.show(cb, text, kb.ik(*rows))
