"""
GSS MCP Server - API Key Authentication

API keys are SHA-256 hashed and stored in a separate SQLite database
(gss_api_keys.db). This database is never committed to the repo.

Rate limiting is per-key with daily reset. Tiers control daily limits:
  free: 100, professional: 1000, enterprise: 10000
"""

import hashlib
import sqlite3
from datetime import date

from config import API_KEYS_DB

TIER_LIMITS = {"free": 100, "professional": 1000, "enterprise": 10000}


def _connect():
    conn = sqlite3.connect(API_KEYS_DB)
    conn.row_factory = sqlite3.Row
    return conn


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def validate_key(raw_key: str) -> dict | None:
    """
    Returns key record dict if valid and within rate limit.
    Returns None if invalid, inactive, or rate-limited.
    Resets daily counter if last_reset_date != today.
    """
    conn = _connect()
    hashed = hash_key(raw_key)

    row = conn.execute(
        "SELECT * FROM api_keys WHERE api_key = ? AND active = 1", (hashed,)
    ).fetchone()

    if not row:
        conn.close()
        return None

    today = date.today().isoformat()
    if row["last_reset_date"] != today:
        conn.execute(
            "UPDATE api_keys SET requests_today = 0, last_reset_date = ? WHERE key_id = ?",
            (today, row["key_id"]),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key_id = ?", (row["key_id"],)
        ).fetchone()

    if row["requests_today"] >= row["daily_limit"]:
        conn.close()
        return None

    conn.execute(
        "UPDATE api_keys SET requests_today = requests_today + 1 WHERE key_id = ?",
        (row["key_id"],),
    )
    conn.commit()
    conn.close()
    return dict(row)


def create_key(label: str, tier: str = "free") -> str:
    """Generate a new API key and store hashed version. Returns raw key."""
    import secrets

    raw_key = f"gss_{secrets.token_urlsafe(32)}"
    hashed = hash_key(raw_key)

    conn = _connect()
    conn.execute(
        "INSERT INTO api_keys (api_key, key_label, tier, daily_limit) VALUES (?,?,?,?)",
        (hashed, label, tier, TIER_LIMITS.get(tier, 100)),
    )
    conn.commit()
    conn.close()
    return raw_key


def init_db():
    """Create API keys tables if they don't exist."""
    conn = _connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS api_keys (
            key_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key         TEXT NOT NULL UNIQUE,
            key_label       TEXT NOT NULL,
            tier            TEXT NOT NULL DEFAULT 'free',
            daily_limit     INTEGER NOT NULL DEFAULT 100,
            requests_today  INTEGER NOT NULL DEFAULT 0,
            last_reset_date DATE,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            active          INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS request_log (
            log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key_id          INTEGER NOT NULL,
            tool_name       TEXT NOT NULL,
            called_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
            response_ms     INTEGER,
            FOREIGN KEY (key_id) REFERENCES api_keys(key_id)
        );
    """)
    conn.commit()
    conn.close()
