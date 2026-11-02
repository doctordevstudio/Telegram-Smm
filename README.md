# Telegram SMM — Bot + Session Engine + Admin Panel

Telegram bot (aiogram 3) that sells **Subscribers / Views / Reactions / Live-stream viewers**, a safe
session engine (Telethon), and a responsive Gen-Z admin panel — all in **one Python process**, backed by
**Firebase Realtime Database**, built for **Render free + UptimeRobot**.

> ⚠️ **Read this first.** Artificial engagement violates Telegram's Terms of Service. No setup can
> *guarantee* sessions are never limited or banned — this project only **reduces** the risk (slow drip-feed,
> per-session caps, jitter, FloodWait handling, health checks, optional proxies). Use fresh/aged accounts you
> can afford to lose, and keep the default conservative speeds. The **live-stream join is experimental**
> (see Phase 3 notes) — test it with 1–2 sessions on a real stream before selling it.

---

## What you get (all 6 phases)

| Phase | What | Where |
|---|---|---|
| 1 | Bot core: force-join, channel setup, wallet + UPI deposits, 4 order flows, history, admin commands | `app/handlers/*` |
| 2 | Session engine: import, validate, drip-feed jobs, locking, safety, refunds, notifications | `app/engine/*` |
| 3 | Live-stream viewers: instant join, parallel orders, keep-alive, join-only (low bandwidth) | `app/engine/stream.py` |
| 4 | Admin backend (FastAPI): auth, dashboard, plans, payments, broadcast, sessions, logs, settings | `app/web/api.py` |
| 5 | Admin UI (vanilla JS, no build step) | `app/web/static/*` |
| 6 | Deploy: `render.yaml`, env guide, UptimeRobot, hardening | this file |

## Project layout
```
main.py                 FastAPI app: /webhook (bot) · /health · /admin (panel) · /api/*
app/
  config.py db.py security.py utils.py constants.py runtime.py
  handlers/             start, menu, channel, order, wallet, admin (bot commands)
  services/             store (all DB access), availability, forcejoin, resolver, notify, broadcast
  engine/               sessions, allocator, actions, runner, stream, schedule, sessionfmt, errors
  web/                  auth.py, api.py, static/{index.html,style.css,app.js}
render.yaml  requirements.txt  .env.example  .python-version
```

---

## 1) Setup

### Firebase
1. Create a project → **Build → Realtime Database → Create** (any region, locked mode).
2. Rules tab → `{"rules": {".read": false, ".write": false}}` (the Admin SDK bypasses rules, so nobody else can read your data).
3. Project settings → **Service accounts → Generate new private key** → `serviceAccount.json`.
4. Copy the database URL (`https://….firebaseio.com`) into `FIREBASE_DB_URL`.

### Telegram
- Create the bot with @BotFather → `BOT_TOKEN`. Get your numeric chat ID (e.g. via @userinfobot) → `ADMIN_IDS`.
- Create API keys at **my.telegram.org → API development tools** → `API_ID` / `API_HASH`.
  Add **several** (use different accounts/numbers) in Panel → Setting; sessions are spread evenly across them.
- Optional force-join channel: add the bot as **admin** there.

### Run locally
```bash
pip install -r requirements.txt
cp .env.example .env        # fill it in; put serviceAccount.json next to main.py
python main.py              # no BASE_URL → long polling.  Panel: http://localhost:8000/admin
```
First start prints the panel login in the console if you didn't set `ADMIN_PASSWORD`.

---

## 2) Deploy on Render (free web service)

1. Push this folder to a **private** GitHub repo (`.gitignore` already excludes secrets).
2. Render → **New → Blueprint** → select the repo (uses `render.yaml`), or create a *Web Service* manually:
   - Build: `pip install -r requirements.txt`
   - Start: `uvicorn main:api --host 0.0.0.0 --port $PORT --workers 1 --no-access-log`
   - Health check path: `/health`
3. Environment variables (Dashboard → Environment):

| Variable | Notes |
|---|---|
| `BOT_TOKEN`, `ADMIN_IDS`, `FIREBASE_DB_URL` | required |
| `FIREBASE_CRED_JSON` | paste the **whole** service-account JSON (one line) |
| `SESSION_KEY` | long random string — encrypts sessions at rest. **Never change after importing sessions.** |
| `SECRET_KEY` | long random string — signs panel cookies |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | panel login (password is stored hashed; change it later in Setting) |
| `API_ID` / `API_HASH` | optional first API pair |
| `UPI_ID`, `SUPPORT_USERNAME`, `FORCE_CHANNEL` | optional (editable in the panel) |
| `PYTHON_VERSION` | `3.11.9` |

   Render injects `RENDER_EXTERNAL_URL`, so the webhook is registered automatically on boot.
4. **UptimeRobot** → New monitor → HTTP(s) → `https://<your-app>.onrender.com/health` → interval **5 minutes**.
   This keeps the free instance awake (and the order engine running). Without pings Render sleeps after ~15 min.
5. Open `https://<your-app>.onrender.com/admin` and log in.

### First-time checklist
1. Panel → **Setting**: UPI ID, upload QR, support username, API ID/hash, force-join channel.
2. Panel → **Session**: drag & drop your `.session` files (or paste string sessions).
3. In the bot (as admin) send `/start` — the new-user alert + menu appear.
4. Test an order end-to-end with a small plan on a channel **you own**.

---

## 3) Using the bot (users)
`/start` → join force channel → welcome → send channel link (public or private invite) →
**Home**: Get Subscribers · Views · Reactions · Live Stream Viewers. Bottom menu (3 per row):
Home · My Channel · Plan · Wallet · History · Customer Support. Every screen has **Cancel**.

Deposits: pick ₹50/100/150/200 or custom (min ₹50) → QR + UPI ID → *Submit Payment Screenshot* → admin gets
the proof with **Approve / Reject** (reject asks for a reason; user is notified either way). Manual verification
text says 2–3 hours.

### Bot admin commands
`/ban id` · `/unban id` · `/broadcast` · `/send id` · `/setupi` · `/setqr` · `/setsupport` · `/setforce` · `/sessions` · `/admin`

---

## 4) Admin panel
Drawer: **Home · Plans · Payments · Broadcast · Session · Logs · Setting**; 20 rows per page everywhere.

- **Home** — users (tap card → paginated list, ban / balance / message), earning, purchases, spend, 14-day chart,
  which plans sell, purchases by whom. Filters: Today · Yesterday · All time · Custom range.
- **Plans** — one tab per service: edit price-per-1 (+ min/max, optional recalculation of all plans), add / edit /
  hide / delete plans, see which plan was bought and by whom.
- **Payments** — received / pending / approved / failed, screenshots, approve or reject from the panel.
- **Broadcast** — text + image/video/file; list by date range with received counts; **rebroadcast, edit, delete**
  (edit/delete also change the messages already delivered to users).
- **Session** — drag & drop many files (checked one by one against Telegram, then saved encrypted), paste strings,
  **Validate all** with progress, per-session check / proxy / delete, pagination. Accepts **Telethon and Pyrogram**
  `.session` files and Telethon string sessions.
- **Logs** — *Bot users* and *Admin panel* tabs.
- **Setting** — password, UPI, QR, multiple API ID/hash pairs, force channel (verifies the bot is admin there),
  session safety & speed.

Footer: `© 2026 Copyright by dekhohatke.com` (blue link, opens in a new tab).

---

## 5) How the engine keeps sessions safe
- **Drip-feed, never bursts.** Each order becomes a persisted job spread over time (default seconds-per-unit in
  Setting). Views honour the user's split (`200 in 30` …, ≤ 1440 min total).
- **Per-session limits:** cooldown between actions, actions/hour, joins/day, random jitter (all editable).
- **FloodWait** → session sleeps for the exact time (+ random extra) and is skipped. **PeerFlood** → rested 24 h.
- **Dead/banned/revoked** sessions are detected automatically and excluded; repeated unknown errors rest the session.
- **One action per session per target**, tracked in `targets/*` (subscribed / viewed / reacted) → availability =
  active sessions − sessions already used on that channel / post / stream. A global lock guarantees two orders never
  receive the same session.
- **Auto-subscribe:** views/reactions/stream first join the channel with the same sessions (shown as subscribers).
- **Encrypted at rest** (Fernet, `SESSION_KEY`). Stable fake device profile per session. Optional **per-session proxy**
  (`socks5://user:pass@host:port`).
- **Resume after restart:** progress is stored per order; the scheduler picks unfinished orders up again.
- **Refunds:** undelivered part is refunded automatically (partial / failed / cancelled) and the user is notified.
- **Low RAM (512 MB):** sessions connect on demand and disconnect right after; `max_clients` (default 4) caps
  simultaneous connections. Live streams are the exception (see below) and capped by `stream_cap` (default 25).

### Phase 3 — live stream notes
- Sessions only **join** the call (muted, no video, no media download) and send a tiny keep-alive every ~25 s →
  minimal bandwidth. One account can be in only one call at a time, so availability is global.
- If a stream isn't live yet the order waits (default 10 min) and refunds if it never starts.
- Several users buying the same stream at once is handled by the allocator lock.
- **Experimental:** joining uses synthetic WebRTC parameters. Telegram may count/kick such participants differently
  over time. Verify on a real stream with 1–2 sessions first; if you need guaranteed presence you'd swap
  `StreamHub._join_one` for a real call stack (e.g. pytgcalls) — all the plumbing around it stays the same.

---

## 6) Data layout (Firebase)
`users · channels · wallets · orders · deposits · plans/{svc} · sessions (meta) · session_data (encrypted) · targets ·
live · broadcasts · broadcast_msgs · logs/{bot|admin} · stats/{totals,daily} · settings · idx/* (small indexes) ·
counters`. Images are never stored in Firebase — only Telegram `file_id`s (QR, proofs, profile pics, broadcast media).

## 7) Troubleshooting
| Symptom | Fix |
|---|---|
| Bot silent on Render | Check logs for `Webhook set`; verify `BOT_TOKEN`; open `/health` |
| `Add at least one API ID` on import | Setting → Telegram API |
| Sessions all "dead" after redeploy | `SESSION_KEY` changed — restore the old value |
| Panel login lost after restart | Set `SECRET_KEY` explicitly |
| Force-join not working | Bot must be **admin** of that channel (Setting → Force channel verifies) |
| Orders stay *queued* | No active sessions / engine paused — check Session page and logs |
| Out-of-memory on Render | Lower `max_clients` and `stream_cap` in Setting |
| Private channel order fails fast | Invite link expired/revoked or is a join-request link |

## 8) Security notes
Panel cookie is HttpOnly + SameSite=Strict, state-changing calls need a custom header, login is rate-limited
(5 tries → 15 min), passwords are scrypt-hashed, sessions encrypted, strict CSP. Keep the repo private and never
commit `.env` / `serviceAccount.json`.

© 2026 dekhohatke.com
