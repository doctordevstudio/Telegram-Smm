from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from .constants import SERVICES
from .roles import is_owner


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def ik(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(t, d) for t, d in row] for row in rows])


def main_menu(uid: int) -> ReplyKeyboardMarkup:
    rows = [["📺 Channels", "➕ Add Channel"], ["📊 Stats", "🧩 Sessions"]]
    if is_owner(uid):
        rows.append(["👥 Admins", "🌐 Proxy"])
    rows.append(["🆘 Help"])
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t) for t in r] for r in rows],
                               resize_keyboard=True, is_persistent=True, input_field_placeholder="Choose an option 👇")


CANCEL_KB = ik([("❌ Cancel", "cancel")])
HOME_ONLY_KB = ik([("🏠 Home", "home")])


def channels_list_kb(channels: list[dict], page: int, pages: int) -> InlineKeyboardMarkup:
    rows = [[(f"{'🟢' if c.get('_active') else '⚪'} {c['title'][:28]}", f"ch:open:{c['id']}")] for c in channels]
    nav = []
    if page > 1:
        nav.append(("⬅️ Prev", f"ch:list:{page - 1}"))
    if pages > 1:
        nav.append((f"📄 {page}/{pages}", "noop"))
    if page < pages:
        nav.append(("Next ➡️", f"ch:list:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("➕ Add Channel", "ch:add")])
    rows.append([("🏠 Home", "home")])
    return ik(*rows)


def channel_panel_kb(cid: str) -> InlineKeyboardMarkup:
    return ik(
        [(f"{SERVICES['views']['emoji']} View Settings", f"sv:{cid}:views"), ("🌊 Wave Mode", f"wv:{cid}")],
        [(f"{SERVICES['reactions']['emoji']} Reaction Control", f"sv:{cid}:reactions"), (f"{SERVICES['subscribers']['emoji']} Subscribers", f"sv:{cid}:subscribers")],
        [(f"{SERVICES['mute']['emoji']} Mute Control", f"sv:{cid}:mute"), (f"{SERVICES['poll']['emoji']} Poll Voting", f"sv:{cid}:poll")],
        [("🌙 Night Mode", f"nm:{cid}"), ("⏳ Set Delay", f"dl:{cid}")],
        [("📈 Stats & Info", f"st:{cid}"), (f"{SERVICES['stream']['emoji']} Live Stream", f"sv:{cid}:stream")],
        [("🔁 Renew Channel", f"rn:{cid}"), ("🧨 Revoke All", f"rva:{cid}")],
        [("🗑 Remove Channel", f"chdel:{cid}")],
        [("⬅️ Back to Channels", "ch:list:1")],
    )


def confirm_kb(yes_cb: str, no_cb: str = "cancel", yes_text: str = "✅ Confirm", no_text: str = "❌ Cancel") -> InlineKeyboardMarkup:
    return ik([(yes_text, yes_cb)], [(no_text, no_cb)])


def back_kb(cb: str, text: str = "⬅️ Back") -> InlineKeyboardMarkup:
    return ik([(text, cb)])


def service_intro_kb(cid: str, svc: str, enabled: bool) -> InlineKeyboardMarkup:
    rows = []
    if enabled:
        rows.append([("⚙️ Configure", f"svcfg:{cid}:{svc}"), ("🔁 Extend Days", f"svext:{cid}:{svc}")])
        rows.append([("🧨 Revoke", f"svrev:{cid}:{svc}")])
    else:
        rows.append([("✅ Enable Service", f"sven:{cid}:{svc}")])
    rows.append([("⬅️ Back to Channel", f"ch:open:{cid}")])
    return ik(*rows)


def channel_days_kb() -> InlineKeyboardMarkup:
    from .constants import PRESET_DAYS
    rows, row = [], []
    for d in PRESET_DAYS:
        row.append((f"{d} days", f"chdays:{d}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("✏️ Custom Days", "chdays:custom")])
    rows.append([("❌ Cancel", "cancel")])
    return ik(*rows)


def days_kb(cid: str, svc: str, presets: list[int]) -> InlineKeyboardMarkup:
    rows, row = [], []
    for d in presets:
        row.append((f"{d} days", f"svdays:{cid}:{svc}:{d}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("✏️ Custom Days", f"svdays:{cid}:{svc}:custom")])
    rows.append([("❌ Cancel", f"sv:{cid}:{svc}")])
    return ik(*rows)


def numeric_kb(cid: str, field: str, presets: list[float], current: float | None, *, unit: str = "",
              extra: list[tuple[str, str]] | None = None, back_cb: str = "", cols: int = 3) -> InlineKeyboardMarkup:
    rows, row = [], []
    for v in presets:
        label = f"{'🔵' if current == v else '⚪'}{v:g}{unit}"
        row.append((label, f"np:{cid}:{field}:{v:g}"))
        if len(row) == cols:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if extra:
        for pair in extra:
            rows.append([pair])
    rows.append([("✏️ Custom", f"np:{cid}:{field}:custom")])
    if back_cb:
        rows.append([("⬅️ Back", back_cb)])
    return ik(*rows)


def toggle_kb(cid: str, field: str, enabled: bool, back_cb: str) -> InlineKeyboardMarkup:
    return ik([(("🟢 Enabled (tap to disable)" if enabled else "⚪ Disabled (tap to enable)"), f"tg:{cid}:{field}")],
             [("⬅️ Back", back_cb)])


def reaction_mix_kb(cid: str, pool: list[str], mix: dict[str, float]) -> InlineKeyboardMarkup:
    rows, row = [], []
    for e in pool:
        pct = mix.get(e)
        label = f"{e} {int(pct)}%" if pct else e
        row.append((label, f"rmx:{cid}:{e}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("♻️ Reset to System Random", f"rmx:{cid}:reset")])
    rows.append([("⬅️ Back", f"svcfg:{cid}:reactions")])
    return ik(*rows)


def pct_kb(cid: str, emoji: str, back_cb: str) -> InlineKeyboardMarkup:
    from .constants import PRESET_PCT
    rows, row = [], []
    for p in PRESET_PCT:
        row.append((f"{p}%", f"rmxp:{cid}:{emoji}:{p}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("⬅️ Back", back_cb)])
    return ik(*rows)


def admins_kb(admins: list[dict]) -> InlineKeyboardMarkup:
    rows = [[(f"👤 {a.get('username') or a['id']} — remove", f"admrm:{a['id']}")] for a in admins]
    rows.append([("➕ Add Admin", "admadd")])
    rows.append([("🏠 Home", "home")])
    return ik(*rows)


def pager_kb(prefix: str, page: int, pages: int, extra: list | None = None) -> InlineKeyboardMarkup:
    nav = []
    if page > 1:
        nav.append(("⬅️ Prev", f"{prefix}:{page - 1}"))
    nav.append((f"📄 {page}/{pages}", "noop"))
    if page < pages:
        nav.append(("Next ➡️", f"{prefix}:{page + 1}"))
    return ik(nav, extra or [("🏠 Home", "home")])
