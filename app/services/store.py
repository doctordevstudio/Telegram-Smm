"""All Firebase data access (channels, services, settings, logs). No wallet, no public users, no
payments — the bot is an internal tool for the owner and the admins the owner grants access to.

Nothing is ever hard-deleted. "Removing" a channel, a service, an admin or a session only sets a
`hidden` flag; the record stays in the database forever and the owner can still see it via the
audit views in the admin panel (`list_*_all` helpers / Logs page).
"""
from __future__ import annotations

import asyncio
import logging

from .. import db
from ..config import cfg
from ..constants import DEFAULT_CHANNEL_SETTINGS, DEFAULT_SAFETY, DEFAULT_SERVICE_CONFIG, SERVICE_ORDER
from ..utils import day_key, now, spawn

log = logging.getLogger("store")


def _safe(key: str) -> str:
    if not key or not str(key).replace("_", "").replace("-", "").isalnum():
        raise ValueError("bad key")
    return str(key)


def _merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    out.update({k: v for k, v in patch.items() if v is not None})
    return out


# ───────────── settings ─────────────
async def get_settings() -> dict:
    return await db.cached_get("settings", ttl=15) or {}


async def set_setting(key: str, value) -> None:
    await db.put(f"settings/{key}", value)
    db.invalidate("settings")


async def get_safety() -> dict:
    return {**DEFAULT_SAFETY, **((await get_settings()).get("safety") or {})}


async def get_apis() -> list[dict]:
    raw = (await get_settings()).get("apis") or {}
    return [{"id": int(k), "hash": v["hash"]} for k, v in raw.items() if isinstance(v, dict) and v.get("hash")]


async def get_proxy() -> dict:
    p = (await get_settings()).get("proxy") or {}
    return {"enabled": bool(p.get("enabled")), "url": p.get("url", "")}


# ───────────── channels ─────────────
async def add_channel(rec: dict, by: int) -> str:
    cid = f"ch{await db.next_id('channels'):06d}"
    rec = {**rec, "id": cid, "added_by": by, "added_at": now(), "hidden": False}
    await db.put(f"channels/{cid}", rec)
    db.invalidate("channels")
    return cid


async def get_channel(cid: str, *, include_hidden: bool = False) -> dict | None:
    c = await db.get(f"channels/{_safe(cid)}")
    if not c or (c.get("hidden") and not include_hidden):
        return None
    return c


async def list_channels(*, include_hidden: bool = False) -> list[dict]:
    raw = await db.cached_get("channels", ttl=10) or {}
    items = [v for v in raw.values() if isinstance(v, dict)]
    if not include_hidden:
        items = [v for v in items if not v.get("hidden")]
    return sorted(items, key=lambda c: c.get("added_at", 0), reverse=True)


async def find_channel_by_ref(chat_id: int | None, username: str | None, invite_hash: str | None) -> dict | None:
    for c in await list_channels(include_hidden=True):
        if chat_id and c.get("chat_id") == chat_id:
            return c
        if username and (c.get("username") or "").lower() == username.lower():
            return c
        if invite_hash and c.get("invite_hash") == invite_hash:
            return c
    return None


async def update_channel(cid: str, patch: dict) -> None:
    await db.update(f"channels/{_safe(cid)}", patch)
    db.invalidate("channels")


async def hide_channel(cid: str, by: int) -> bool:
    c = await get_channel(cid)
    if not c:
        return False
    await update_channel(cid, {"hidden": True, "hidden_at": now(), "hidden_by": by})
    return True


async def unhide_channel(cid: str, by: int) -> bool:
    c = await get_channel(cid, include_hidden=True)
    if not c:
        return False
    await update_channel(cid, {"hidden": False, "restored_at": now(), "restored_by": by})
    return True


# ───────────── channel-level settings (delay / night mode / wave mode) ─────────────
async def get_channel_settings(cid: str) -> dict:
    raw = await db.get(f"channel_settings/{_safe(cid)}") or {}
    out = {**DEFAULT_CHANNEL_SETTINGS, **raw}
    out["night_mode"] = {**DEFAULT_CHANNEL_SETTINGS["night_mode"], **(raw.get("night_mode") or {})}
    out["wave_mode"] = {**DEFAULT_CHANNEL_SETTINGS["wave_mode"], **(raw.get("wave_mode") or {})}
    return out


async def update_channel_settings(cid: str, patch: dict) -> None:
    await db.update(f"channel_settings/{_safe(cid)}", patch)


# ───────────── services (subscribers / views / reactions / mute / poll / stream) ─────────────
def _svc(svc: str) -> str:
    if svc not in SERVICE_ORDER:
        raise ValueError(f"unknown service {svc}")
    return svc


async def get_service(cid: str, svc: str) -> dict:
    raw = await db.get(f"services/{_safe(cid)}/{_svc(svc)}") or {}
    cfg_out = {**DEFAULT_SERVICE_CONFIG[svc], **(raw.get("config") or {})}
    return {"enabled": bool(raw.get("enabled")), "expires_at": raw.get("expires_at"), "days_total": raw.get("days_total"),
            "rate_note": raw.get("rate_note", ""), "started_by": raw.get("started_by"), "started_at": raw.get("started_at"),
            "revoked": bool(raw.get("revoked")), "config": cfg_out}


async def get_all_services(cid: str) -> dict[str, dict]:
    res = await asyncio.gather(*(get_service(cid, s) for s in SERVICE_ORDER))
    return dict(zip(SERVICE_ORDER, res))


async def enable_service(cid: str, svc: str, days: int, config: dict, rate_note: str, by: int) -> None:
    _svc(svc)
    cfg_merged = {**DEFAULT_SERVICE_CONFIG[svc], **config}
    rec = {"enabled": True, "days_total": days, "expires_at": now() + days * 86400, "rate_note": rate_note,
           "started_by": by, "started_at": now(), "revoked": False, "config": cfg_merged}
    await db.put(f"services/{_safe(cid)}/{svc}", rec)


async def update_service_config(cid: str, svc: str, patch: dict) -> None:
    cur = (await get_service(cid, svc))["config"]
    await db.put(f"services/{_safe(cid)}/{_svc(svc)}/config", {**cur, **patch})


async def pause_service(cid: str, svc: str, paused: bool) -> None:
    await update_service_config(cid, svc, {"paused": paused})


async def extend_service(cid: str, svc: str, extra_days: int, by: int) -> int:
    cur = await get_service(cid, svc)
    base = cur["expires_at"] if cur["expires_at"] and cur["expires_at"] > now() else now()
    new_exp = base + extra_days * 86400
    await db.update(f"services/{_safe(cid)}/{_svc(svc)}", {"expires_at": new_exp,
                     "days_total": (cur.get("days_total") or 0) + extra_days, "enabled": True, "revoked": False,
                     "extended_by": by, "extended_at": now()})
    return new_exp


async def revoke_service(cid: str, svc: str, by: int) -> None:
    """Soft: disables the service and marks it revoked. The configuration/history record is kept forever."""
    await db.update(f"services/{_safe(cid)}/{_svc(svc)}", {"enabled": False, "revoked": True, "revoked_at": now(),
                                                            "revoked_by": by})


async def revoke_all_services(cid: str, by: int) -> list[str]:
    done = []
    for svc in SERVICE_ORDER:
        cur = await get_service(cid, svc)
        if cur["enabled"]:
            await revoke_service(cid, svc, by)
            done.append(svc)
    return done


# ───────────── post tracking (continuous "all upcoming posts" delivery, not single-post purchase) ─────────────
async def record_post(cid: str, mid: int, has_poll: bool = False, poster_id: int | None = None) -> None:
    await db.put(f"posts/{_safe(cid)}/{mid}", {"detected_at": now(), "has_poll": has_poll, "poster_id": poster_id})


async def get_post(cid: str, mid: int) -> dict | None:
    return await db.get(f"posts/{_safe(cid)}/{mid}")


async def update_post(cid: str, mid: int, patch: dict) -> None:
    await db.update(f"posts/{_safe(cid)}/{mid}", patch)


async def pending_posts(cid: str, max_age_sec: int = 3 * 86400) -> list[dict]:
    raw = await db.get(f"posts/{_safe(cid)}") or {}
    cutoff = now() - max_age_sec
    return sorted([v for v in raw.values() if isinstance(v, dict) and v.get("detected_at", 0) > cutoff],
                  key=lambda p: p.get("detected_at", 0))


# ───────────── subscriber / mute continuous progress ─────────────
async def get_progress(cid: str, kind: str) -> dict:
    return await db.get(f"progress/{_safe(cid)}/{_safe(kind)}") or {}


async def update_progress(cid: str, kind: str, patch: dict) -> None:
    await db.update(f"progress/{_safe(cid)}/{_safe(kind)}", patch)


# ───────────── logs (never trimmed, source = bot | panel) ─────────────
async def log_event(source: str, uid: int | None, role: str, action: str, detail: str = "",
                    channel_id: str | None = None) -> None:
    await db.push("logs/actions", {"ts": now(), "uid": uid, "role": role, "source": source, "action": action,
                                   "detail": detail[:400], "channel_id": channel_id})


def log_bg(source: str, uid: int | None, role: str, action: str, detail: str = "", channel_id: str | None = None) -> None:
    spawn(log_event(source, uid, role, action, detail, channel_id))
