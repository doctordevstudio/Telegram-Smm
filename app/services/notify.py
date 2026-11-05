from __future__ import annotations

import logging

from aiogram.exceptions import TelegramForbiddenError

from ..config import cfg
from ..roles import ADMINS

log = logging.getLogger("notify")


async def notify_staff(bot, text: str, markup=None) -> None:
    """Owner + every active admin get operational alerts (session dead, service expired, stream issue...)."""
    for uid in [cfg.owner_id, *ADMINS.keys()]:
        try:
            await bot.send_message(uid, text, reply_markup=markup)
        except TelegramForbiddenError:
            pass
        except Exception as e:  # noqa: BLE001
            log.warning("notify failed for %s: %s", uid, e)


async def notify_owner(bot, text: str, markup=None) -> None:
    try:
        await bot.send_message(cfg.owner_id, text, reply_markup=markup)
    except Exception as e:  # noqa: BLE001
        log.warning("notify owner failed: %s", e)
