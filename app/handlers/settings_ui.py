"""Per-channel service configuration screens: enable/extend/revoke a service, and edit every numeric
or toggle knob shown in the reference Channel Control Panel (view speed/qty, reaction mix, mute
actions, poll voting, night mode, process delay, wave mode, subscriber pace, live-stream knobs).

A small generic "numeric picker" (presets + custom) and "toggle" dispatcher (FIELD_MAP below) drive
most of these screens so the ~15 near-identical settings pages don't need 15 near-identical handlers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from .. import ui
from ..constants import (PRESET_DAYS, PRESET_DELAY, PRESET_INTERVAL, PRESET_MEMBERS, PRESET_PCT, PRESET_QTY,
                         PRESET_RANGE, PRESET_SPEED, PRESET_VOTES, REACTION_EMOJIS, SERVICES)
from ..filters import TextNoCmd
from ..roles import role_of
from ..services import store
from ..states import NumSt, ServiceSt
from ..utils import esc, fmt_days_left, fmt_seconds, now

router = Router(name="settings_ui")


# ───────────── generic field registry ─────────────
@dataclass
class Field:
    kind: str                 # "service" | "channel"
    svc: str | None           # service name, when kind == "service"
    path: tuple[str, ...]     # key path inside its config dict
    presets: list[float]
    unit: str
    lo: float
    hi: float
    is_int: bool
    renderer: str
    extra: list[tuple[str, str]] | None = None


FIELD_MAP: dict[str, Field] = {
    "delay": Field("channel", None, ("delay_seconds",), PRESET_DELAY, "s", 0, 21600, True, "delay"),
    "night_mult": Field("channel", None, ("night_mode", "multiplier"), [1, 1.5, 2, 3, 4, 5], "x", 1, 10, False, "night"),
    "night_start": Field("channel", None, ("night_mode", "start_hour"), list(range(0, 24, 2)), "h", 0, 23, True, "night"),
    "night_end": Field("channel", None, ("night_mode", "end_hour"), list(range(0, 24, 2)), "h", 0, 23, True, "night"),
    "views_speed": Field("service", "views", ("speed_seconds",), PRESET_SPEED, "s", 0.5, 3600, False, "views"),
    "views_qty": Field("service", "views", ("qty",), PRESET_QTY, "", 1, 200000, True, "views"),
    "views_range": Field("service", "views", ("random_range",), PRESET_RANGE, "", 1, 200000, True, "views"),
    "react_speed": Field("service", "reactions", ("speed_seconds",), PRESET_SPEED, "s", 0.5, 3600, False, "reactions"),
    "react_qty": Field("service", "reactions", ("qty",), PRESET_QTY, "", 1, 200000, True, "reactions"),
    "mute_speed": Field("service", "mute", ("speed_seconds",), PRESET_SPEED, "s", 0.5, 3600, False, "mute"),
    "mute_daily": Field("service", "mute", ("daily_actions",), [10, 20, 30, 40, 50, 60, 70, 80, 90, 100], "", 1, 1000, True, "mute"),
    "poll_speed": Field("service", "poll", ("speed_seconds",), PRESET_SPEED, "s", 0.5, 3600, False, "poll"),
    "poll_votes": Field("service", "poll", ("max_votes",), PRESET_VOTES, "", 1, 1000000, True, "poll",
                        extra=[("♾ Unlimited", "np:{cid}:poll_votes:0")]),
    "stream_speed": Field("service", "stream", ("join_speed",), [0.5, 1, 1.5, 2, 3, 5], "s", 0.3, 60, False, "stream"),
    "stream_members": Field("service", "stream", ("max_members",), PRESET_MEMBERS, "", 1, 500, True, "stream"),
    "stream_hand": Field("service", "stream", ("hand_rise_pct",), PRESET_PCT, "%", 0, 100, True, "stream"),
    "sub_interval": Field("service", "subscribers", ("interval_seconds",), PRESET_INTERVAL, "s", 1, 86400, True, "subscribers"),
    "sub_target": Field("service", "subscribers", ("target_count",), [50, 100, 200, 500, 1000, 2000], "", 0, 10_000_000, True,
                        "subscribers", extra=[("♾ Until Expiry (no cap)", "np:{cid}:sub_target:0")]),
}
TOGGLE_MAP: dict[str, Field] = {
    "random_enabled": Field("service", "views", ("random_enabled",), [], "", 0, 1, True, "views"),
    "skip_poster": Field("service", "reactions", ("skip_poster",), [], "", 0, 1, True, "reactions"),
    "night_enabled": Field("channel", None, ("night_mode", "enabled"), [], "", 0, 1, True, "night"),
    "wave_enabled": Field("channel", None, ("wave_mode", "enabled"), [], "", 0, 1, True, "wave"),
}


async def _get_channel_settings_value(cid: str, path: tuple[str, ...]):
    s = await store.get_channel_settings(cid)
    for k in path[:-1]:
        s = s.get(k, {})
    return s.get(path[-1])


async def _set_channel_settings_value(cid: str, path: tuple[str, ...], value) -> None:
    s = await store.get_channel_settings(cid)
    cur = s
    for k in path[:-1]:
        cur = cur.setdefault(k, {})
    cur[path[-1]] = value
    patch = {}
    if len(path) == 1:
        patch[path[0]] = value
    else:
        patch[path[0]] = s[path[0]]
    await store.update_channel_settings(cid, patch)


async def get_field(cid: str, f: Field):
    if f.kind == "channel":
        return await _get_channel_settings_value(cid, f.path)
    cfgd = (await store.get_service(cid, f.svc))["config"]
    cur = cfgd
    for k in f.path[:-1]:
        cur = cur.get(k, {})
    return cur.get(f.path[-1])


async def set_field(cid: str, f: Field, value) -> None:
    if f.kind == "channel":
        return await _set_channel_settings_value(cid, f.path, value)
    cfgd = (await store.get_service(cid, f.svc))["config"]
    cur = cfgd
    for k in f.path[:-1]:
        cur = cur.setdefault(k, {})
    cur[f.path[-1]] = value
    await store.update_service_config(cid, f.svc, {f.path[0]: cfgd[f.path[0]]})


RENDERERS: dict[str, Callable[[Any, str], Awaitable[None]]] = {}  # populated at the bottom of this file


async def redraw(ev, renderer: str, cid: str) -> None:
    fn = RENDERERS.get(renderer)
    if fn:
        await fn(ev, cid)


# ───────────── generic numeric picker ─────────────
@router.callback_query(F.data.startswith("np:"))
async def np_cb(cb: CallbackQuery, state: FSMContext):
    _, cid, field, val = cb.data.split(":", 3)
    f = FIELD_MAP.get(field)
    if not f:
        return await cb.answer()
    if val == "custom":
        await state.set_state(NumSt.value)
        await state.update_data(cid=cid, field=field)
        await cb.answer()
        unit = f" ({f.unit})" if f.unit else ""
        return await ui.show(cb, f"✏️ Send a number{unit} between {f.lo:g} and {f.hi:g}:",
                             kb.back_kb(f"__redraw__{f.renderer}:{cid}"))
    await cb.answer()
    await _apply_numeric(cb, cid, f, float(val))


@router.message(NumSt.value, TextNoCmd())
async def np_text(m: Message, state: FSMContext):
    d = await state.get_data()
    cid, field = d.get("cid"), d.get("field")
    f = FIELD_MAP.get(field) if field else None
    if not cid or not f:
        await state.clear()
        return
    raw = m.text.strip().replace(",", ".")
    try:
        val = float(raw)
    except ValueError:
        return await m.answer(f"❌ Send a plain number between {f.lo:g} and {f.hi:g}.")
    if not (f.lo <= val <= f.hi):
        return await m.answer(f"❌ Must be between {f.lo:g} and {f.hi:g}.")
    await state.clear()
    await _apply_numeric(m, cid, f, val)


async def _apply_numeric(ev, cid: str, f: Field, val: float) -> None:
    val = int(val) if f.is_int else round(val, 2)
    await set_field(cid, f, val)
    uid = ev.from_user.id
    store.log_bg("bot", uid, role_of(uid), "config_set", f"{f.svc or 'channel'}.{'.'.join(f.path)}={val}", cid)
    await redraw(ev, f.renderer, cid)


# ───────────── generic toggle ─────────────
@router.callback_query(F.data.startswith("tg:"))
async def toggle_cb(cb: CallbackQuery):
    _, cid, field = cb.data.split(":", 2)
    f = TOGGLE_MAP.get(field)
    if not f:
        return await cb.answer()
    cur = bool(await get_field(cid, f))
    await set_field(cid, f, not cur)
    uid = cb.from_user.id
    store.log_bg("bot", uid, role_of(uid), "toggle", f"{field}={not cur}", cid)
    await cb.answer("Updated")
    await redraw(cb, f.renderer, cid)


# ───────────── enable / extend / revoke a service ─────────────
@router.callback_query(F.data.startswith("sv:"))
async def service_intro(cb: CallbackQuery):
    _, cid, svc = cb.data.split(":")
    await cb.answer()
    s = await store.get_service(cid, svc)
    title = SERVICES[svc]["title"]
    if s["enabled"]:
        text = (f"{SERVICES[svc]['emoji']} <b>{title}</b>\n\n🟢 Status: <b>ENABLED</b>\n⏳ {fmt_days_left(s['expires_at'])}")
        if s.get("rate_note"):
            text += f"\n💰 Rate: {esc(s['rate_note'])}"
    else:
        text = f"{SERVICES[svc]['emoji']} <b>{title}</b>\n\n⚪ Status: <b>DISABLED</b>\n\nEnable it to start configuring."
    await ui.show(cb, text, kb.service_intro_kb(cid, svc, s["enabled"]))


@router.callback_query(F.data.startswith("sven:"))
async def service_enable(cb: CallbackQuery):
    _, cid, svc = cb.data.split(":")
    await cb.answer()
    await ui.show(cb, f"📅 How many days should <b>{SERVICES[svc]['title']}</b> run on <b>all upcoming posts</b>?",
                 kb.days_kb(cid, svc, PRESET_DAYS))


@router.callback_query(F.data.startswith("svdays:"))
async def service_days(cb: CallbackQuery, state: FSMContext):
    _, cid, svc, val = cb.data.split(":")
    if val == "custom":
        await state.set_state(ServiceSt.days)
        await state.update_data(cid=cid, svc=svc)
        await cb.answer()
        return await ui.show(cb, "✏️ Send the number of days (1-3650):", kb.back_kb(f"sv:{cid}:{svc}"))
    await cb.answer()
    await state.update_data(cid=cid, svc=svc, days=int(val))
    await state.set_state(ServiceSt.rate)
    await ui.show(cb, "💰 Optional: send a short note about the price/rate you charged for this "
                      "(e.g. <code>₹200 for 20 days</code>), or send <code>-</code> to skip.", kb.CANCEL_KB)


@router.message(ServiceSt.days, TextNoCmd())
async def service_days_text(m: Message, state: FSMContext):
    d = await state.get_data()
    cid, svc = d.get("cid"), d.get("svc")
    if not cid:
        return
    raw = m.text.strip()
    if not raw.isdigit() or not (1 <= int(raw) <= 3650):
        return await m.answer("❌ Send a whole number of days between 1 and 3650.", reply_markup=kb.back_kb(f"sv:{cid}:{svc}"))
    await state.update_data(days=int(raw))
    await state.set_state(ServiceSt.rate)
    await m.answer("💰 Optional: send a short note about the price/rate you charged for this "
                   "(e.g. <code>₹200 for 20 days</code>), or send <code>-</code> to skip.", reply_markup=kb.CANCEL_KB)


@router.message(ServiceSt.rate, TextNoCmd())
async def service_rate_text(m: Message, state: FSMContext):
    d = await state.get_data()
    cid, svc, days = d.get("cid"), d.get("svc"), d.get("days")
    if not cid or not svc or not days:
        await state.clear()
        return
    rate = "" if m.text.strip() == "-" else m.text.strip()[:200]
    await state.clear()
    uid = m.from_user.id
    await store.enable_service(cid, svc, days, {}, rate, uid)
    store.log_bg("bot", uid, role_of(uid), "service_enabled", f"{svc} {days}d {rate}", cid)
    await m.answer(f"✅ <b>{SERVICES[svc]['title']}</b> enabled for <b>{days} days</b> on all upcoming posts!")
    await redraw(m, RENDER_KEY.get(svc, svc), cid)


@router.callback_query(F.data.startswith("svext:"))
async def service_extend(cb: CallbackQuery):
    _, cid, svc = cb.data.split(":")
    await cb.answer()
    rows, row = [], []
    for d in PRESET_DAYS:
        row.append((f"+{d} days", f"svextd:{cid}:{svc}:{d}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([("⬅️ Back", f"sv:{cid}:{svc}")])
    await ui.show(cb, f"🔁 Extend <b>{SERVICES[svc]['title']}</b> by how many days?", kb.ik(*rows))


@router.callback_query(F.data.startswith("svextd:"))
async def service_extend_go(cb: CallbackQuery):
    _, cid, svc, days = cb.data.split(":")
    uid = cb.from_user.id
    new_exp = await store.extend_service(cid, svc, int(days), uid)
    store.log_bg("bot", uid, role_of(uid), "service_extended", f"{svc} +{days}d", cid)
    await cb.answer("Extended")
    await ui.show(cb, f"✅ Extended. {fmt_days_left(new_exp)}", kb.ik([("⬅️ Back", f"sv:{cid}:{svc}")]))


@router.callback_query(F.data.startswith("svrev:"))
async def service_revoke(cb: CallbackQuery):
    _, cid, svc = cb.data.split(":")
    await cb.answer()
    await ui.show(cb, f"🧨 Revoke <b>{SERVICES[svc]['title']}</b>? Its configuration is kept, just disabled.",
                 kb.confirm_kb(f"svrevgo:{cid}:{svc}", f"sv:{cid}:{svc}"))


@router.callback_query(F.data.startswith("svrevgo:"))
async def service_revoke_go(cb: CallbackQuery):
    _, cid, svc = cb.data.split(":")
    uid = cb.from_user.id
    await store.revoke_service(cid, svc, uid)
    store.log_bg("bot", uid, role_of(uid), "service_revoked", svc, cid)
    await cb.answer("Revoked")
    await ui.show(cb, f"🧨 {SERVICES[svc]['title']} revoked.", kb.ik([("⬅️ Back", f"ch:open:{cid}")]))


@router.callback_query(F.data.startswith("svcfg:"))
async def service_configure(cb: CallbackQuery):
    _, cid, svc = cb.data.split(":")
    await cb.answer()
    await redraw(cb, RENDER_KEY.get(svc, svc), cid)


RENDER_KEY = {"subscribers": "subscribers", "views": "views", "reactions": "reactions", "mute": "mute",
             "poll": "poll", "stream": "stream"}


# ───────────── per-service render screens ─────────────
async def _header(cid: str, svc: str) -> tuple[dict, dict]:
    c = await store.get_channel(cid)
    s = await store.get_service(cid, svc)
    return c, s


async def render_subscribers(ev, cid: str) -> None:
    c, s = await _header(cid, "subscribers")
    cfgd = s["config"]
    from .. import db
    from ..engine.campaigns import _chan_key
    base = _chan_key(c) if c else ""
    cur = len(await db.keys(f"targets/{base}/subs")) if base else 0
    target = cfgd.get("target_count") or 0
    text = (f"👥 <b>Subscribers</b>\n\n📺 Channel: {esc(c['title'])}\n⏳ {fmt_days_left(s['expires_at'])}\n\n"
           f"🙋 Current subscribers (from our sessions): <b>{cur}</b>\n"
           f"🎯 Target: <b>{'Until Expiry (no cap)' if not target else target}</b>\n"
           f"⏱ Interval: every <b>{fmt_seconds(cfgd.get('interval_seconds', 60))}</b>, one at a time")
    rows = [[("⏱ Edit Interval", f"edit:sub_interval:{cid}"), ("🎯 Edit Target", f"edit:sub_target:{cid}")],
           [("⬅️ Back", f"ch:open:{cid}")]]
    await ui.show(ev, text, kb.ik(*rows))


async def render_views(ev, cid: str) -> None:
    c, s = await _header(cid, "views")
    cfgd = s["config"]
    text = (f"👁 <b>View Settings</b>\n\n📺 Channel: {esc(c['title'])}\n⏳ {fmt_days_left(s['expires_at'])}\n\n"
           f"🔢 Quantity per post: <b>{cfgd.get('qty')}</b>\n⏱ Speed: every <b>{fmt_seconds(cfgd.get('speed_seconds', 5))}</b>\n"
           f"🎲 Random Views: {'🟢 ±' + str(cfgd.get('random_range')) if cfgd.get('random_enabled') else '⚪ off'}")
    rows = [[("🔢 Edit Quantity", f"edit:views_qty:{cid}"), ("⚡ Edit Speed", f"edit:views_speed:{cid}")],
           [("🎲 Random Range", f"edit:views_range:{cid}"), (("🟢 Random: ON" if cfgd.get("random_enabled") else "⚪ Random: OFF"), f"tg:{cid}:random_enabled")],
           [("⬅️ Back", f"ch:open:{cid}")]]
    await ui.show(ev, text, kb.ik(*rows))


async def render_reactions(ev, cid: str) -> None:
    c, s = await _header(cid, "reactions")
    cfgd = s["config"]
    mix = cfgd.get("mix") or {}
    mix_txt = ", ".join(f"{e} {int(p)}%" for e, p in mix.items()) or "System Random (even mix)"
    text = (f"❤️ <b>Reaction Control</b>\n\n📺 Channel: {esc(c['title'])}\n⏳ {fmt_days_left(s['expires_at'])}\n\n"
           f"🔢 Quantity per post: <b>{cfgd.get('qty')}</b>\n⏱ Speed: every <b>{fmt_seconds(cfgd.get('speed_seconds', 15))}</b>\n"
           f"⏭ Skip poster: {'🟢 on' if cfgd.get('skip_poster') else '⚪ off'}\n🎭 Mix: {esc(mix_txt)}")
    rows = [[("🔢 Edit Quantity", f"edit:react_qty:{cid}"), ("⚡ Edit Speed", f"edit:react_speed:{cid}")],
           [("🎭 Emoji Mix", f"rmxopen:{cid}"), (("⏭ Skip Poster: ON" if cfgd.get("skip_poster") else "⏭ Skip Poster: OFF"), f"tg:{cid}:skip_poster")],
           [("⬅️ Back", f"ch:open:{cid}")]]
    await ui.show(ev, text, kb.ik(*rows))


async def render_mute(ev, cid: str) -> None:
    c, s = await _header(cid, "mute")
    cfgd = s["config"]
    from ..utils import day_key
    from ..config import cfg as appcfg
    prog = await store.get_progress(cid, "mute")
    today_count = prog.get("count", 0) if prog.get("day") == day_key(appcfg.timezone) else 0
    text = (f"🔇 <b>Mute Control</b>\n\n📺 Channel: {esc(c['title'])}\n⏳ {fmt_days_left(s['expires_at'])}\n\n"
           f"📊 Daily actions: <b>{cfgd.get('daily_actions')}</b> (done today: {today_count})\n"
           f"⏱ Speed: every <b>{fmt_seconds(cfgd.get('speed_seconds', 30))}</b>")
    rows = [[("📊 Edit Daily Actions", f"edit:mute_daily:{cid}"), ("⚡ Edit Speed", f"edit:mute_speed:{cid}")],
           [("⬅️ Back", f"ch:open:{cid}")]]
    await ui.show(ev, text, kb.ik(*rows))


async def render_poll(ev, cid: str) -> None:
    c, s = await _header(cid, "poll")
    cfgd = s["config"]
    votes = cfgd.get("max_votes") or 0
    text = (f"📊 <b>Poll Voting</b>\n\n📺 Channel: {esc(c['title'])}\n⏳ {fmt_days_left(s['expires_at'])}\n\n"
           f"🗳 Max votes per poll: <b>{'Unlimited' if not votes else votes}</b>\n⏱ Speed: every <b>{fmt_seconds(cfgd.get('speed_seconds', 30))}</b>\n\n"
           f"<i>Votes a random option whenever a poll appears in a new post.</i>")
    rows = [[("🗳 Edit Max Votes", f"edit:poll_votes:{cid}"), ("⚡ Edit Speed", f"edit:poll_speed:{cid}")],
           [("⬅️ Back", f"ch:open:{cid}")]]
    await ui.show(ev, text, kb.ik(*rows))


async def render_stream(ev, cid: str) -> None:
    c, s = await _header(cid, "stream")
    cfgd = s["config"]
    from .. import runtime
    live = 0
    if runtime.engine:
        live = len(runtime.engine.stream.by_channel.get(cid, ()))
    paused = cfgd.get("paused")
    text = (f"🔴 <b>Live Stream Viewers</b>\n\n📺 Channel: {esc(c['title'])}\n⏳ {fmt_days_left(s['expires_at'])}\n\n"
           f"🎯 Target viewers: <b>{cfgd.get('max_members')}</b>\n🟢 Currently connected: <b>{live}</b>\n"
           f"🖐 Hand rise: {cfgd.get('hand_rise_pct', 0)}%\n⚡ Join speed: every ~{fmt_seconds(cfgd.get('join_speed', 1.5))}\n"
           f"⏯ Status: {'⏸ PAUSED' if paused else '▶️ RUNNING'}")
    if not (runtime.engine and runtime.engine.stream.available):
        text += "\n\n⚠️ pytgcalls isn't available on this server — live-stream viewers are disabled until it is."
    rows = [[("🎯 Edit Target", f"edit:stream_members:{cid}"), ("⚡ Edit Join Speed", f"edit:stream_speed:{cid}")],
           [("🖐 Edit Hand Rise %", f"edit:stream_hand:{cid}")],
           [(("▶️ Resume" if paused else "⏸ Pause"), f"stpause:{cid}:{0 if paused else 1}")],
           [("⬅️ Back", f"ch:open:{cid}")]]
    await ui.show(ev, text, kb.ik(*rows))


async def render_night(ev, cid: str) -> None:
    c = await store.get_channel(cid)
    s = await store.get_channel_settings(cid)
    nm = s["night_mode"]
    text = (f"🌙 <b>Night Mode</b>\n\n📺 Channel: {esc(c['title'])}\n\nSlows views/reactions down during quiet "
           f"hours so delivery looks natural.\n\nStatus: {'🟢 ENABLED' if nm['enabled'] else '⚪ DISABLED'}\n"
           f"🕙 Active hours: {nm['start_hour']:02d}:00 – {nm['end_hour']:02d}:00\n✖️ Multiplier: {nm['multiplier']}x slower")
    rows = [[("🕙 Start Hour", f"edit:night_start:{cid}"), ("🕕 End Hour", f"edit:night_end:{cid}")],
           [("✖️ Multiplier", f"edit:night_mult:{cid}")],
           [(("🟢 Enabled" if nm["enabled"] else "⚪ Disabled") + " (tap to toggle)", f"tg:{cid}:night_enabled")],
           [("⬅️ Back", f"ch:open:{cid}")]]
    await ui.show(ev, text, kb.ik(*rows))


async def render_wave(ev, cid: str) -> None:
    c = await store.get_channel(cid)
    s = await store.get_channel_settings(cid)
    wv = s["wave_mode"]
    text = ("🌊 <b>Wave Mode</b>\n\n" + f"📺 Channel: {esc(c['title'])}\n\n"
           "Makes delivery speed taper off naturally as a post ages, like organic traffic:\n\n"
           "🚀 0–1 hour: full speed\n🚶 1–3 hours: 1.5x slower\n🐢 3+ hours: 2x slower\n\n"
           f"Status: {'🟢 ENABLED' if wv['enabled'] else '⚪ DISABLED'}")
    rows = [[(("🟢 Enabled" if wv["enabled"] else "⚪ Disabled") + " (tap to toggle)", f"tg:{cid}:wave_enabled")],
           [("⬅️ Back", f"ch:open:{cid}")]]
    await ui.show(ev, text, kb.ik(*rows))


async def render_delay(ev, cid: str) -> None:
    c = await store.get_channel(cid)
    s = await store.get_channel_settings(cid)
    text = (f"⏳ <b>Process Delay</b>\n\n📺 Channel: {esc(c['title'])}\n\nThe bot waits this long after a new post "
           f"appears before starting views/reactions/polls on it (keeps delivery from looking instant).\n\n"
           f"Current delay: <b>{fmt_seconds(s['delay_seconds'])}</b>")
    await ui.show(ev, text, kb.numeric_kb(cid, "delay", PRESET_DELAY, s["delay_seconds"], unit="s", back_cb=f"ch:open:{cid}"))


RENDERERS.update({"subscribers": render_subscribers, "views": render_views, "reactions": render_reactions,
                 "mute": render_mute, "poll": render_poll, "stream": render_stream, "night": render_night,
                 "wave": render_wave, "delay": render_delay})


# ───────────── "edit:<field>:<cid>" shortcut used by the per-service screens above ─────────────
CHANNEL_FIELD_BACK = {"delay": "dl", "night": "nm", "wave": "wv"}


@router.callback_query(F.data.startswith("edit:"))
async def edit_field(cb: CallbackQuery):
    _, field, cid = cb.data.split(":")
    f = FIELD_MAP.get(field)
    if not f:
        return await cb.answer()
    await cb.answer()
    cur = await get_field(cid, f)
    extra = [(t, c.format(cid=cid)) for t, c in (f.extra or [])]
    back_cb = f"sv:{cid}:{f.svc}" if f.svc else f"{CHANNEL_FIELD_BACK.get(f.renderer, 'ch:open')}:{cid}"
    await ui.show(cb, f"Select a value (current: <b>{cur}{f.unit}</b>):",
                 kb.numeric_kb(cid, field, f.presets, cur, unit=f.unit, extra=extra, back_cb=back_cb))


# night/wave/delay back buttons route through their own renderer directly
@router.callback_query(F.data.startswith("nm:"))
async def night_cb(cb: CallbackQuery):
    await cb.answer()
    await render_night(cb, cb.data.split(":", 1)[1])


@router.callback_query(F.data.startswith("wv:"))
async def wave_cb(cb: CallbackQuery):
    await cb.answer()
    await render_wave(cb, cb.data.split(":", 1)[1])


@router.callback_query(F.data.startswith("dl:"))
async def delay_cb(cb: CallbackQuery):
    await cb.answer()
    await render_delay(cb, cb.data.split(":", 1)[1])


@router.callback_query(F.data.startswith("__redraw__"))
async def redraw_cb(cb: CallbackQuery):
    renderer, cid = cb.data[len("__redraw__"):].split(":", 1)
    await cb.answer()
    await redraw(cb, renderer, cid)


# ───────────── reaction emoji mix ─────────────
@router.callback_query(F.data.startswith("rmxopen:"))
async def rmx_open(cb: CallbackQuery):
    cid = cb.data.split(":", 1)[1]
    await cb.answer()
    s = await store.get_service(cid, "reactions")
    pool = (await store.get_channel(cid) or {}).get("reactions_allowed") or REACTION_EMOJIS
    mix = s["config"].get("mix") or {}
    total = sum(mix.values())
    text = (f"🎭 <b>Reaction Emoji Mix</b>\n\nTap an emoji to set its share. Unassigned emojis split the "
           f"remaining probability evenly (\"System Random\").\n\n📊 Currently assigned: <b>{int(total)}%</b>")
    await ui.show(cb, text, kb.reaction_mix_kb(cid, pool, mix))


@router.callback_query(F.data.startswith("rmx:"))
async def rmx_pick(cb: CallbackQuery):
    _, cid, emoji = cb.data.split(":", 2)
    await cb.answer()
    if emoji == "reset":
        await store.update_service_config(cid, "reactions", {"mix": {}})
        uid = cb.from_user.id
        store.log_bg("bot", uid, role_of(uid), "reaction_mix_reset", "", cid)
        return await rmx_open(cb)
    pool = (await store.get_channel(cid) or {}).get("reactions_allowed") or REACTION_EMOJIS
    await ui.show(cb, f"Set {emoji} to what percentage?", kb.pct_kb(cid, emoji, f"rmxopen:{cid}"))


@router.callback_query(F.data.startswith("rmxp:"))
async def rmx_set(cb: CallbackQuery):
    _, cid, emoji, pct = cb.data.split(":")
    s = await store.get_service(cid, "reactions")
    mix = dict(s["config"].get("mix") or {})
    if float(pct) <= 0:
        mix.pop(emoji, None)
    else:
        mix[emoji] = float(pct)
    await store.update_service_config(cid, "reactions", {"mix": mix})
    uid = cb.from_user.id
    store.log_bg("bot", uid, role_of(uid), "reaction_mix_set", f"{emoji}={pct}%", cid)
    await cb.answer(f"{emoji} set to {pct}%")
    await rmx_open(cb)


# ───────────── live stream pause/resume ─────────────
@router.callback_query(F.data.startswith("stpause:"))
async def stream_pause(cb: CallbackQuery):
    _, cid, val = cb.data.split(":")
    await store.update_service_config(cid, "stream", {"paused": bool(int(val))})
    uid = cb.from_user.id
    store.log_bg("bot", uid, role_of(uid), "stream_pause", val, cid)
    await cb.answer("Updated")
    await render_stream(cb, cid)
