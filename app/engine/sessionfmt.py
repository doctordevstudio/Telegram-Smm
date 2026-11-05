"""Convert Telethon / Pyrogram `.session` files to Telethon StringSession text (pure python, no telethon needed)."""
from __future__ import annotations

import base64
import ipaddress
import os
import sqlite3
import struct
import tempfile

DC_IPS = {1: "149.154.175.53", 2: "149.154.167.51", 3: "149.154.175.100", 4: "149.154.167.91", 5: "91.108.56.130"}


def encode_string(dc_id: int, ip: str, port: int, key: bytes) -> str:
    if len(key) != 256:
        raise ValueError("auth key must be 256 bytes")
    packed = ipaddress.ip_address(ip).packed
    return "1" + base64.urlsafe_b64encode(struct.pack(f">B{len(packed)}sH256s", dc_id, packed, port, key)).decode()


def decode_string(s: str) -> tuple[int, str, int, bytes]:
    s = s.strip()
    if not s or s[0] != "1":
        raise ValueError("not a Telethon string session")
    data = base64.urlsafe_b64decode(s[1:] + "=" * (-len(s[1:]) % 4))
    n = 4 if len(data) == 263 else 16 if len(data) == 275 else 0
    if not n:
        raise ValueError("bad string session length")
    dc, ip, port, key = struct.unpack(f">B{n}sH256s", data)
    return dc, str(ipaddress.ip_address(ip)), port, key


def from_sqlite_bytes(raw: bytes) -> str:
    fd, path = tempfile.mkstemp(suffix=".session")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        return from_sqlite(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def from_sqlite(path: str) -> str:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise ValueError(f"not a sqlite session file: {e}") from e
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
        if not cols:
            raise ValueError("no `sessions` table - not a Telethon/Pyrogram session")
        if "server_address" in cols:  # Telethon
            row = con.execute("SELECT dc_id, server_address, port, auth_key FROM sessions LIMIT 1").fetchone()
            if not row or not row[3]:
                raise ValueError("session file has no auth key (never logged in)")
            dc, ip, port, key = row
        else:  # Pyrogram
            row = con.execute("SELECT dc_id, auth_key FROM sessions LIMIT 1").fetchone()
            if not row or not row[1]:
                raise ValueError("session file has no auth key (never logged in)")
            dc, key = row
            ip, port = DC_IPS.get(dc, ""), 443
            if not ip:
                raise ValueError(f"unknown DC {dc}")
        return encode_string(int(dc), ip, int(port), bytes(key))
    except sqlite3.Error as e:
        raise ValueError(f"unreadable session file: {e}") from e
    finally:
        con.close()
