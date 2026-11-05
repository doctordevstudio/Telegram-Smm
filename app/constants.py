from __future__ import annotations

# ---- services offered per channel (no per-post purchase - these apply to ALL upcoming posts) ----
SERVICE_ORDER = ["subscribers", "views", "reactions", "mute", "poll", "stream"]

SERVICES = {
    "subscribers": {"emoji": "👥", "title": "Subscribers"},
    "views": {"emoji": "👁", "title": "Views"},
    "reactions": {"emoji": "❤️", "title": "Reactions"},
    "mute": {"emoji": "🔇", "title": "Mute/Unmute Actions"},
    "poll": {"emoji": "📊", "title": "Poll Voting"},
    "stream": {"emoji": "🔴", "title": "Live Stream Viewers"},
}

# ---- numeric picker presets (seconds unless noted) ----
PRESET_SPEED = [0.5, 1, 2, 3, 5, 10, 15, 30, 60]
PRESET_DELAY = [0, 10, 30, 60, 120, 300, 600, 1800, 3600]
PRESET_QTY = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
PRESET_RANGE = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
PRESET_VOTES = [50, 100, 200, 300, 400, 500, 600, 700, 900]
PRESET_MEMBERS = [5, 10, 20, 30, 40, 50, 60, 80, 100]
PRESET_PCT = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
PRESET_DAYS = [7, 15, 20, 30, 60, 90]
PRESET_INTERVAL = [5, 10, 15, 30, 60, 120, 300, 600, 1800]

# ---- engine defaults (editable in admin panel -> Settings / bot -> per-channel) ----
DEFAULT_SAFETY = {
    "actions_per_hour": 15, "joins_per_day": 20, "cooldown_sec": 45, "max_clients": 4, "batch_max": 6,
    "jitter_min": 2, "jitter_max": 9, "stream_cap": 25, "stream_max_minutes": 240, "stream_wait_minutes": 10,
    "stream_concurrency": 3,
}
DEFAULT_SERVICE_CONFIG = {
    "subscribers": {"interval_seconds": 60, "target_count": 0},                       # 0 = unlimited until expiry
    "views": {"qty": 30, "speed_seconds": 5, "random_enabled": False, "random_range": 20},
    "reactions": {"qty": 20, "speed_seconds": 15, "skip_poster": False, "mix": {}},    # mix empty = system random
    "mute": {"daily_actions": 10, "speed_seconds": 30},
    "poll": {"speed_seconds": 30, "max_votes": 0},                                    # 0 = unlimited
    "stream": {"max_members": 25, "hand_rise_pct": 0, "join_speed": 1.5, "paused": False},
}
DEFAULT_CHANNEL_SETTINGS = {
    "delay_seconds": 10,                                                              # Set Delay / process delay
    "night_mode": {"enabled": False, "start_hour": 22, "end_hour": 6, "multiplier": 2},
    "wave_mode": {"enabled": False},                                                  # 0-1h full,1-3h x1.5,3h+ x2
}

REACTION_EMOJIS = [
    "❤", "🔥", "👍", "🤩", "😍", "🙌", "🎉", "⚡", "💯", "🤣", "🚀", "💔", "😢", "🕊", "👏", "🌚",
    "🎄", "💅", "🗿", "🆒", "💘", "🙊", "😘", "💊", "🙏", "👌", "🤡", "👎", "😐", "🥱", "🥳", "😱",
]
SHOW_VS16 = {"❤", "🕊"}

PAGE_SIZE_BOT = 8
PAGE_SIZE_ADMIN = 20
