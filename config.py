"""
GSS MCP Server - Configuration

Loads settings from .env file. Database paths point to the main GSS pipeline's
output files (read-only). The MCP server reads from both:
  1. SQLite databases (gss_history.db) for historical queries
  2. Exported JSON files (snapshot.json, alerts.json, etc.) for current state

This avoids replicating k-factor scoring logic — export_site_data.py pre-computes
all k-variant scores, movers, alert events, and domain scores.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Database paths (read-only) ──────────────────────────────────────────────

HISTORY_DB = os.environ["GSS_HISTORY_DB"]
API_KEYS_DB = os.environ["GSS_API_KEYS_DB"]

# ── Exported data paths (read-only) ─────────────────────────────────────────
# These are the JSON files produced by export_site_data.py in the main GSS repo.
# Default: relative to HISTORY_DB location (../site/public/data/)

_default_data_dir = os.path.join(
    os.path.dirname(HISTORY_DB), "..", "site", "public", "data"
)
DATA_DIR = os.environ.get("GSS_DATA_DIR", _default_data_dir)

SNAPSHOT_JSON = os.path.join(DATA_DIR, "snapshot.json")
HISTORY_JSON = os.path.join(DATA_DIR, "gss_history.json")
ALERTS_JSON = os.path.join(DATA_DIR, "alerts.json")
SIGNALS_INDEX_JSON = os.path.join(DATA_DIR, "signals", "index.json")
SIGNAL_HISTORY_DIR = os.path.join(DATA_DIR, "history")
NARRATIVE_JSON = os.path.join(DATA_DIR, "narrative.json")

# ── Server settings ─────────────────────────────────────────────────────────

HOST = os.environ.get("MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("MCP_PORT", "8765"))
SERVER_NAME = os.environ.get("MCP_SERVER_NAME", "gss-mcp")
SERVER_VERSION = os.environ.get("MCP_SERVER_VERSION", "0.1.0")

# ── GA4 Measurement Protocol (optional) ───────────────────────────────────

GA4_MEASUREMENT_ID = os.environ.get("GA4_MEASUREMENT_ID", "")
GA4_API_SECRET = os.environ.get("GA4_API_SECRET", "")
