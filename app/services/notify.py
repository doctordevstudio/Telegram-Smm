from __future__ import annotations

import logging
from contextlib import suppress

from aiogram.exceptions import TelegramForbiddenError

from ..config import cfg
from ..constants import ORDER_STATUS, SERVICES
from ..utils import esc, money

log = logging.getLogger("notify")


async def notify_admins(bot, text: str, *, photo: str | None = None, markup=None) -> None:
    for aid in cfg.admin_ids:
        try:
            if photo:
                await bot.send_photo(aid, photo, caption=text, reply_markup=markup)
            else:
                await bot.send_message(aid, text, reply_markup=markup)
        except Exception as e:  # noqa: BLE001
            log.warning("admin notify failed for %s: %s", aid, e)
            if photo:
                with suppress(Exception):
                    await bot.send_message(aid, text, reply_markup=markup)


async def notify_user(bot, uid: int, text: str, markup=None) -> bool:
    try:
        await bot.send_message(uid, text, reply_markup=markup)
        return True
    except TelegramForbiddenError:
        return False
    except Exception as e:  # noqa: BLE001
        log.warning("user notify failed for %s: %s", uid, e)
        return False


async def notify_order_status(bot, order: dict) -> bool:
    """Called by the session engine (Phase 2) when an order finishes / fails."""
    s = SERVICES[order["service"]]
    emoji, label = ORDER_STATUS.get(order["status"], ("ℹ️", order["status"]))
    text = (f"{emoji} <b>Order #{int(order['id'])} — {label}</b>\n\n"
            f"{s['emoji']} {s['title']}: <b>{int(order.get('delivered', order['qty'])):,}</b> / {int(order['qty']):,}\n"
            f"💵 Paid: {money(order['price'])}")
    if order.get("note"):
        text += f"\n📝 {esc(order['note'])}"
    return await notify_user(bot, order["user_id"], text)
