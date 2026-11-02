from __future__ import annotations

SERVICE_ORDER = ["subscribers", "views", "reactions", "stream"]

SERVICES = {
    "subscribers": {"emoji": "👥", "title": "Subscribers", "unit": "subscriber", "units": "subscribers"},
    "views": {"emoji": "👁", "title": "Views", "unit": "view", "units": "views"},
    "reactions": {"emoji": "❤️", "title": "Reactions", "unit": "reaction", "units": "reactions"},
    "stream": {"emoji": "🔴", "title": "Live Stream Viewers", "unit": "viewer", "units": "viewers"},
}

# Defaults seeded on first run. Everything is editable later from the admin panel.
DEFAULT_PRICING = {
    "subscribers": {"unit": 1.5, "min": 10, "max": 10000},
    "views": {"unit": 0.1, "min": 100, "max": 100000},
    "reactions": {"unit": 0.5, "min": 10, "max": 5000},
    "stream": {"unit": 2.0, "min": 10, "max": 1000},
}
DEFAULT_PLAN_QTYS = {
    "subscribers": [50, 100, 250, 500, 1000],
    "views": [500, 1000, 5000, 10000],
    "reactions": [25, 50, 100, 250],
    "stream": [25, 50, 100, 200],
}

ORDER_STATUS = {
    "queued": ("🕒", "Queued"),
    "running": ("⚙️", "In Progress"),
    "completed": ("✅", "Completed"),
    "partial": ("🟠", "Partially Completed"),
    "failed": ("❌", "Failed"),
    "cancelled": ("🚫", "Cancelled"),
}
DEPOSIT_STATUS = {
    "pending": ("🟡", "Pending"),
    "approved": ("🟢", "Approved"),
    "rejected": ("🔴", "Failed"),
}

PAGE_SIZE = 10
MIN_DEPOSIT = 50
MAX_DEPOSIT = 50000
DEPOSIT_PRESETS = (50, 100, 150, 200)
MAX_PENDING_DEPOSITS = 3
MAX_SPLIT_MINUTES = 24 * 60

BTN_HOME = "🏠 Home"
BTN_CHANNEL = "📺 My Channel"
BTN_PLAN = "💎 Plan"
BTN_WALLET = "💰 Wallet"
BTN_HISTORY = "📜 History"
BTN_SUPPORT = "🎧 Customer Support"

# Telegram's standard reaction set (variation selectors stripped, see utils.norm_emoji)
_RAW_REACTIONS = (
    "👍 👎 ❤ 🔥 🥰 👏 😁 🤔 🤯 😱 🤬 😢 🎉 🤩 🤮 💩 🙏 👌 🕊 🤡 🥱 🥴 😍 🐳 🌚 🌭 💯 🤣 ⚡ 🍌 🏆 💔 "
    "🤨 😐 🍓 🍾 💋 🖕 😈 😴 😭 🤓 👻 👀 🎃 🙈 😇 😨 🤝 ✍ 🤗 🫡 🎅 🎄 ☃ 💅 🤪 🗿 🆒 💘 🙉 🦄 😘 💊 "
    "🙊 😎 👾 🤷 😡"
).split()
REACTION_EMOJIS = {e.replace("\ufe0f", "") for e in _RAW_REACTIONS} | {
    "\u2764\u200d\U0001f525",  # ❤‍🔥
    "\U0001f468\u200d\U0001f4bb",  # 👨‍💻
    "\U0001f937\u200d\u2642",  # 🤷‍♂
    "\U0001f937\u200d\u2640",  # 🤷‍♀
}
# emojis that render as black text-glyphs unless VS16 is appended
SHOW_VS16 = {"❤", "✍", "☃", "🕊"}

# ---- engine defaults (editable in admin panel -> Settings) ----
DEFAULT_SAFETY = {
    "actions_per_hour": 15,   # max actions per session per hour
    "joins_per_day": 20,      # max channel joins per session per 24h
    "cooldown_sec": 45,       # min gap between two actions of the same session
    "max_clients": 4,         # simultaneous Telegram connections (RAM!)
    "batch_max": 6,           # actions started per scheduler step per order
    "jitter_min": 2,          # random delay range between actions (sec)
    "jitter_max": 9,
    "stall_minutes": 30,      # give up (partial + refund) if no session usable for this long
    "stream_cap": 25,         # max sessions kept connected for live streams
    "stream_max_minutes": 240,
    "stream_wait_minutes": 10,  # how long to wait for a stream to go live
    "stream_concurrency": 3,
}
# seconds per unit when the user does not choose a split (drip-feed speed)
DEFAULT_SPEED = {"subscribers": 15, "views": 2, "reactions": 5, "stream": 1.5}
