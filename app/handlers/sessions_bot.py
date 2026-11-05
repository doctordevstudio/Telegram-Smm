"""Session management from inside the bot itself (owner + admins): upload a .session file or paste
a string session as a message, validate, or remove (soft-delete, never a hard delete)."""
from __future__ import annotations

from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from .. import ui
from ..constants import PAGE_SIZE_BOT
from ..roles import role_of
from ..services import store
from ..utils import esc, fmt_ts, paginate

router = Router(name="sessions_bot")


def _status_emoji(st: str) -> str:
    return {"active": "🟢", "dead": "🔴", "removed": "⚪"}.get(st, "🟡")


async def _list_text(page: int):
    from .. import runtime
    if not runtime.engine:
        return "Engine not ready yet.", None, 1, 1
    items = sorted(runtime.engine.sessions.meta.values(), key=lambda m: m.get("added_at", 0), reverse=True)
    chunk, page, pages = paginate(items, page, PAGE_SIZE_BOT)
    c = runtime.engine.sessions.counts()
    lines = [f"🧩 <b>Sessions</b> — 🟢 {c['active']} active, 🔴 {c['dead']} dead, 🟡 {c.get('flood', 0)} resting", ""]
    for s in chunk:
        lines.append(f"{_status_emoji(s.get('status'))} {esc(s.get('label') or s['id'])} "
                     f"{'@' + esc(s['username']) if s.get('username') else ''}")
    return "\n".join(lines), chunk, page, pages


@router.message(F.text == "🧩 Sessions")
async def m_sessions(m: Message, state: FSMContext):
    await state.clear()
    text, chunk, page, pages = await _list_text(1)
    text += ("\n\n📥 Send a <b>.session file</b> or paste a <b>string session</b> here to add one.\n"
            "🌐 Full management (bulk import, proxy per session) is also in the admin panel.")
    await m.answer(text, reply_markup=kb.pager_kb("sesspage", page, pages, [("✅ Validate All", "sessvalall")]))


@router.callback_query(F.data.startswith("sesspage:"))
async def sesspage_cb(cb: CallbackQuery):
    await cb.answer()
    text, chunk, page, pages = await _list_text(int(cb.data.split(":")[1]))
    await ui.show(cb, text, kb.pager_kb("sesspage", page, pages, [("✅ Validate All", "sessvalall")]))


@router.callback_query(F.data == "sessvalall")
async def sessvalall_cb(cb: CallbackQuery):
    from .. import runtime
    if not runtime.engine:
        return await cb.answer()
    ids = list(runtime.engine.sessions.meta.keys())
    if runtime.engine.sessions.validation["running"]:
        return await cb.answer("Already checking…", show_alert=True)
    from ..utils import spawn
    spawn(runtime.engine.sessions.validate_many(ids))
    store.log_bg("bot", cb.from_user.id, role_of(cb.from_user.id), "session_validate_all", f"{len(ids)} sessions")
    await cb.answer(f"Checking {len(ids)} sessions…", show_alert=True)


@router.message(F.document)
async def got_session_file(m: Message, bot: Bot):
    from .. import runtime
    if not runtime.engine:
        return
    fn = m.document.file_name or "session"
    if not fn.lower().endswith((".session", ".txt", ".string")):
        return
    if (m.document.file_size or 0) > 5 * 1024 * 1024:
        return await m.answer("❌ File too large (max 5 MB).")
    status = await m.answer(f"📥 Importing {esc(fn)}…")
    f = await bot.get_file(m.document.file_id)
    buf = await bot.download_file(f.file_path)
    r = await runtime.engine.sessions.import_file(fn, buf.read(5 * 1024 * 1024 + 1))
    if r["ok"]:
        with suppress(Exception):
            await m.delete()  # session files are account credentials - don't leave them in the chat
    store.log_bg("bot", m.from_user.id, role_of(m.from_user.id), "session_import", f"{fn}: {'ok' if r['ok'] else r['error']}")
    await status.edit_text(f"✅ Imported {esc(r.get('username') or r.get('id'))}" if r["ok"] else f"❌ {esc(r['error'])}")


@router.message(F.text.regexp(r"^1[A-Za-z0-9_-]{100,}={0,2}$"))
async def got_session_string(m: Message):
    from .. import runtime
    if not runtime.engine:
        return
    status = await m.answer("📥 Importing session string…")
    r = await runtime.engine.sessions.import_string(m.text.strip())
    store.log_bg("bot", m.from_user.id, role_of(m.from_user.id), "session_import", "string: " + ("ok" if r["ok"] else r["error"]))
    with suppress(Exception):
        await m.delete()  # the message IS the account's login key - remove it from the chat history
    await status.edit_text(f"✅ Imported {esc(r.get('username') or r.get('id'))}" if r["ok"] else f"❌ {esc(r['error'])}")
