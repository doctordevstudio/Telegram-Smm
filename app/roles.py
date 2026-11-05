"""Owner / admin access control. The bot is now an internal tool: only the owner and the admins the
owner grants access to may use it. There is no public/customer-facing flow any more."""
from __future__ import annotations

from . import db
from .config import cfg
from .utils import now, spawn

ADMINS: dict[int, dict] = {}  # uid -> {name, username, added_by, added_at}  (loaded at boot, soft-delete aware)


def is_owner(uid: int) -> bool:
    return uid == cfg.owner_id


def is_staff(uid: int) -> bool:
    return is_owner(uid) or uid in ADMINS


def role_of(uid: int) -> str:
    return "owner" if is_owner(uid) else "admin" if uid in ADMINS else "none"


async def load() -> None:
    raw = await db.get("admins", {}) or {}
    ADMINS.clear()
    for uid, rec in raw.items():
        if isinstance(rec, dict) and not rec.get("hidden"):
            ADMINS[int(uid)] = rec


async def ensure_owner_record(name: str, username: str) -> None:
    await db.update("owner", {"id": cfg.owner_id, "name": name, "username": username or "", "seen_at": now()})


async def add_admin(uid: int, name: str, username: str, by: int) -> bool:
    if uid == cfg.owner_id or uid in ADMINS:
        return False
    rec = {"id": uid, "name": name, "username": username or "", "added_by": by, "added_at": now(), "hidden": False}
    await db.put(f"admins/{uid}", rec)
    ADMINS[uid] = rec
    return True


async def remove_admin(uid: int, by: int) -> bool:
    """Soft-delete only: the record stays in the database forever, just hidden from bot/panel."""
    if uid not in ADMINS:
        return False
    await db.update(f"admins/{uid}", {"hidden": True, "hidden_at": now(), "hidden_by": by})
    ADMINS.pop(uid, None)
    return True


async def list_admins_visible() -> list[dict]:
    raw = await db.get("admins", {}) or {}
    return sorted([v for v in raw.values() if isinstance(v, dict) and not v.get("hidden")],
                  key=lambda a: a.get("added_at", 0), reverse=True)


async def list_admins_all() -> list[dict]:
    """Owner-only audit view: includes removed admins too (nothing is ever actually deleted)."""
    raw = await db.get("admins", {}) or {}
    return sorted([v for v in raw.values() if isinstance(v, dict)], key=lambda a: a.get("added_at", 0), reverse=True)


def actor_label(uid: int) -> str:
    if is_owner(uid):
        return "Owner"
    a = ADMINS.get(uid)
    return f"Admin({a.get('username') or uid})" if a else f"Unknown({uid})"


def log_action(uid: int, action: str, detail: str = "") -> None:
    """Fire-and-forget audit log entry. Logs are NEVER trimmed or deleted."""
    spawn(db.push("logs/actions", {"ts": now(), "uid": uid, "role": role_of(uid), "action": action,
                                   "detail": detail[:400]}))
