from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int
    base_url: str
    webhook_secret: str
    firebase_db_url: str
    firebase_cred_json: str
    firebase_cred_file: str
    timezone: str
    port: int
    session_key: str
    secret_key: str
    admin_username: str
    admin_password: str
    api_id: str
    api_hash: str
    proxy_url: str


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")
    owner_raw = os.getenv("OWNER_ID", "").strip()
    if not owner_raw.lstrip("-").isdigit():
        raise RuntimeError("OWNER_ID is not set (your numeric Telegram chat ID - the only person who can "
                           "log into the admin panel and the first person the bot answers)")
    db_url = os.getenv("FIREBASE_DB_URL", "").strip()
    if not db_url:
        raise RuntimeError("FIREBASE_DB_URL is not set")
    base = (os.getenv("BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
    secret = os.getenv("WEBHOOK_SECRET") or hashlib.sha256(("wh:" + token).encode()).hexdigest()[:48]
    if not os.getenv("SESSION_KEY") or not os.getenv("SECRET_KEY"):
        logging.getLogger("config").warning(
            "SESSION_KEY / SECRET_KEY are not set - falling back to keys derived from BOT_TOKEN. Set long random "
            "values (NOTE: changing SESSION_KEY later makes already-imported sessions undecryptable).")
    return Config(
        bot_token=token,
        owner_id=int(owner_raw),
        base_url=base,
        webhook_secret=secret,
        firebase_db_url=db_url,
        firebase_cred_json=os.getenv("FIREBASE_CRED_JSON", "").strip(),
        firebase_cred_file=os.getenv("FIREBASE_CRED_FILE", "serviceAccount.json").strip(),
        timezone=os.getenv("TIMEZONE", "Asia/Kolkata").strip(),
        port=int(os.getenv("PORT", "8000")),
        session_key=os.getenv("SESSION_KEY") or "fallback:" + token,
        secret_key=os.getenv("SECRET_KEY") or "secret:" + token,
        admin_username=os.getenv("ADMIN_USERNAME", "owner").strip(),
        admin_password=os.getenv("ADMIN_PASSWORD", "").strip(),
        api_id=os.getenv("API_ID", "").strip(),
        api_hash=os.getenv("API_HASH", "").strip(),
        proxy_url=os.getenv("PROXY_URL", "").strip(),
    )


cfg = load_config()
