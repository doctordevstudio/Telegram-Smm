from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _int_list(raw: str) -> tuple[int, ...]:
    out = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.append(int(part))
    return tuple(out)


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: tuple[int, ...]
    base_url: str
    webhook_secret: str
    firebase_db_url: str
    firebase_cred_json: str
    firebase_cred_file: str
    upi_id: str
    support_username: str
    force_channel: str
    timezone: str
    port: int
    session_key: str
    secret_key: str
    admin_username: str
    admin_password: str
    api_id: str
    api_hash: str


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")
    admins = _int_list(os.getenv("ADMIN_IDS", ""))
    if not admins:
        raise RuntimeError("ADMIN_IDS is not set (comma separated chat IDs)")
    db_url = os.getenv("FIREBASE_DB_URL", "").strip()
    if not db_url:
        raise RuntimeError("FIREBASE_DB_URL is not set")
    base = (os.getenv("BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
    secret = os.getenv("WEBHOOK_SECRET") or hashlib.sha256(token.encode()).hexdigest()[:48]
    return Config(
        bot_token=token,
        admin_ids=admins,
        base_url=base,
        webhook_secret=secret,
        firebase_db_url=db_url,
        firebase_cred_json=os.getenv("FIREBASE_CRED_JSON", "").strip(),
        firebase_cred_file=os.getenv("FIREBASE_CRED_FILE", "serviceAccount.json").strip(),
        upi_id=os.getenv("UPI_ID", "").strip(),
        support_username=os.getenv("SUPPORT_USERNAME", "").strip().lstrip("@"),
        force_channel=os.getenv("FORCE_CHANNEL", "").strip(),
        timezone=os.getenv("TIMEZONE", "Asia/Kolkata").strip(),
        port=int(os.getenv("PORT", "8000")),
        session_key=os.getenv("SESSION_KEY") or "fallback:" + token,
        secret_key=os.getenv("SECRET_KEY") or "secret:" + token,
        admin_username=os.getenv("ADMIN_USERNAME", "admin").strip(),
        admin_password=os.getenv("ADMIN_PASSWORD", "").strip(),
        api_id=os.getenv("API_ID", "").strip(),
        api_hash=os.getenv("API_HASH", "").strip(),
    )


cfg = load_config()
