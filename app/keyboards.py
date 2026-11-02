from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from .constants import (BTN_CHANNEL, BTN_HISTORY, BTN_HOME, BTN_PLAN, BTN_SUPPORT, BTN_WALLET, DEPOSIT_PRESETS,
                        SERVICES)
from .utils import money


def _btn(text: str, data: str) -> InlineKeyboardButton:
    if data.startswith(("http://", "https://", "tg://")):
        return InlineKeyboardButton(text=text, url=data)
    return InlineKeyboardButton(text=text, callback_data=data)


def ik(*rows) -> InlineKeyboardMarkup:
    """ik([('Text','data'), ...], [...]) - data starting with http(s)/tg:// becomes a URL button."""
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(t, d) for t, d in row] for row in rows])


def main_menu() -> ReplyKeyboardMarkup:
    rows = [[BTN_HOME, BTN_CHANNEL, BTN_PLAN], [BTN_WALLET, BTN_HISTORY, BTN_SUPPORT]]  # 3 buttons per row
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in r] for r in rows],
        resize_keyboard=True, is_persistent=True, input_field_placeholder="Choose an option 👇",
    )


CANCEL_KB = ik([("❌ Cancel", "cancel")])
HOME_ONLY_KB = ik([("🏠 Home", "home")])
HOME_KB = ik(
    [("👥 Get Subscribers", "svc:subscribers"), ("👁 Get Views", "svc:views")],
    [("❤️ Get Reactions", "svc:reactions"), ("🔴 Live Stream Viewers", "svc:stream")],
    [("💰 Wallet", "wallet"), ("❓ How to use", "howto")],
)
CHANNEL_KB = ik([("✏️ Edit", "ch:edit"), ("🗑 Delete", "ch:del")], [("🏠 Home", "home")])
CHANNEL_DELETE_KB = ik([("🗑 Yes, delete", "ch:del:yes"), ("↩️ Back", "ch:show")])
AFTER_ORDER_KB = ik([("📜 My Orders", "oh:1"), ("🏠 Home", "home")])
WALLET_KB = ik([("➕ Deposit", "dep:start"), ("🧾 Deposit History", "dh:1")], [("🏠 Home", "home")])
REACT_CONFIRM_KB = ik([("✅ Continue", "react:ok"), ("✏️ Edit", "react:edit")], [("❌ Cancel", "cancel")])
SPLIT_KB = ik(
    [("⚡ Fastest", "split:0"), ("⏳ 1 Hour", "split:60")],
    [("🕕 6 Hours", "split:360"), ("🕛 12 Hours", "split:720")],
    [("📅 24 Hours", "split:1440")],
    [("❌ Cancel", "cancel")],
)
DEPOSIT_AMOUNT_KB = ik(
    [(money(DEPOSIT_PRESETS[0]), f"dep:amt:{DEPOSIT_PRESETS[0]}"), (money(DEPOSIT_PRESETS[1]), f"dep:amt:{DEPOSIT_PRESETS[1]}")],
    [(money(DEPOSIT_PRESETS[2]), f"dep:amt:{DEPOSIT_PRESETS[2]}"), (money(DEPOSIT_PRESETS[3]), f"dep:amt:{DEPOSIT_PRESETS[3]}")],
    [("✏️ Custom Amount", "dep:custom")],
    [("❌ Cancel", "cancel")],
)
DEPOSIT_PAY_KB = ik([("📤 Submit Payment Screenshot", "dep:submit")], [("❌ Cancel", "cancel")])


def plans_kb(plans: list[dict], svc: str) -> InlineKeyboardMarkup:
    emoji = SERVICES[svc]["emoji"]
    rows, row = [], []
    for p in plans:
        row.append((f"{emoji} {p['qty']:,} • {money(p['price'])}", f"plan:{p['id']}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("✏️ Custom Quantity", "custom")])
    rows.append([("❌ Cancel", "cancel")])
    return ik(*rows)


def summary_kb(can_pay: bool, price: float) -> InlineKeyboardMarkup:
    first = [(f"✅ Confirm & Pay {money(price)}", "pay:yes")] if can_pay else [("➕ Add Money", "dep:start")]
    return ik(first, [("❌ Cancel", "cancel")])


def pager_kb(prefix: str, page: int, pages: int, extra: list | None = None) -> InlineKeyboardMarkup:
    nav = []
    if page > 1:
        nav.append(("⬅️ Prev", f"{prefix}:{page - 1}"))
    nav.append((f"📄 {page}/{pages}", "noop"))
    if page < pages:
        nav.append(("Next ➡️", f"{prefix}:{page + 1}"))
    return ik(nav, extra or [("🏠 Home", "home")])


def admin_deposit_kb(key: str) -> InlineKeyboardMarkup:
    return ik([("✅ Approve", f"dep:ok:{key}"), ("❌ Reject", f"dep:no:{key}")])


def join_kb(link: str) -> InlineKeyboardMarkup:
    return ik([("📢 Join Channel", link)], [("✅ I've Joined", "joined")])


def broadcast_kb() -> InlineKeyboardMarkup:
    return ik([("🚀 Send to everyone", "bc:go"), ("❌ Cancel", "bc:cancel")])
