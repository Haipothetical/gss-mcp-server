"""
GSS MCP Server — Grey Swan Signals Model Context Protocol Server

Exposes pre-computed GSS market stress intelligence as MCP tools for AI agents.
Read-only interface layer on top of existing GSS databases and exported JSON.

Transport: HTTP/SSE via Starlette + Uvicorn
Auth: Bearer token API keys (SHA-256 hashed, stored in gss_api_keys.db)
Exposure: Cloudflare Tunnel at mcp.greyswansignals.com

No scoring logic, no data pipeline code, no methodology implementation.
Scores only — no raw data values, no k-factor weights, no signal weights.
"""

import asyncio
import json
import os
from datetime import datetime

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount

import config
import db_reader
import auth

# ── Server Initialization ────────────────────────────────────────────────────

server = Server(config.SERVER_NAME)

# ── Response Helpers ─────────────────────────────────────────────────────────


def _meta() -> dict:
    """Standard metadata included in every response."""
    last_updated = None
    if os.path.exists(config.SNAPSHOT_JSON):
        mtime = os.path.getmtime(config.SNAPSHOT_JSON)
        last_updated = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
    elif os.path.exists(config.HISTORY_DB):
        mtime = os.path.getmtime(config.HISTORY_DB)
        last_updated = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")

    return {
        "last_updated": last_updated,
        "update_cadence": "weekly",
        "scoring_mode": "k15",
        "source": "Grey Swan Signals — greyswansignals.com",
    }


def _respond(data, note: str = None) -> list[TextContent]:
    """Wrap data with metadata and return as JSON TextContent."""
    if data is None:
        payload = {
            "error": "No data found",
            "note": "Signal may not exist or data export may not have run",
        }
    else:
        payload = {"data": data, "meta": _meta()}
        if note:
            payload["meta"]["note"] = note

    return [TextContent(type="text", text=json.dumps(payload, default=str))]


# ── Tool Definitions ─────────────────────────────────────────────────────────

VECTOR_ENUM = [
    "credit-risks", "volatility", "liquidity", "contagion",
    "valuation", "bank-stress",
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_composite_stress_score",
            description=(
                "Returns the current GSS composite market stress score (0-100), "
                "alert tier (BASELINE/WATCH/ALERT/CRITICAL), and week-over-week change. "
                "The composite reflects conditions across Credit Risks, Volatility, "
                "Bank Stress, Contagion, Liquidity, and Valuation vectors. "
                "Includes k-variant scores (k15 production default, k23, k3). "
                "Use this as the first call to establish current market stress context."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_vector_status",
            description=(
                "Returns the current stress reading for a specific GSS risk vector. "
                "Vectors: credit-risks, volatility, liquidity, contagion, valuation, "
                "bank-stress. "
                "Returns vector score, alert level, count of elevated signals, "
                "and individual signal breakdown with Rarity/Velocity components."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vector_slug": {
                        "type": "string",
                        "enum": VECTOR_ENUM,
                        "description": "The GSS risk vector to query (use slugs, not domain names)",
                    }
                },
                "required": ["vector_slug"],
            },
        ),
        Tool(
            name="get_all_vector_scores",
            description=(
                "Returns current scores for all GSS risk vectors in a single call. "
                "Useful for a full market stress overview without making 7 separate calls."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_signal_detail",
            description=(
                "Returns the current reading for a specific GSS signal, including "
                "the score (0-100), alert level, Rarity component (historical percentile), "
                "Velocity component (rate-of-change percentile), and k-variant scores. "
                "Use get_signal_catalog to discover available signal IDs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "signal_id": {
                        "type": "string",
                        "description": "GSS signal identifier (e.g. CF_HY_OAS, MV_VIX, BS_KRE_DRAW)",
                    }
                },
                "required": ["signal_id"],
            },
        ),
        Tool(
            name="get_elevated_signals",
            description=(
                "Returns all GSS signals currently at or above a score threshold. "
                "Default threshold is 51 (ALERT level). Use threshold=76 for CRITICAL only. "
                "Results sorted by score descending."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "threshold": {
                        "type": "integer",
                        "default": 51,
                        "minimum": 0,
                        "maximum": 100,
                        "description": "Minimum score to include (default 51 = ALERT + CRITICAL)",
                    }
                },
                "required": [],
            },
        ),
        Tool(
            name="get_signal_history",
            description=(
                "Returns historical score readings for a specific signal. "
                "Weekly-sampled (every 5th trading day). "
                "Useful for trend analysis and comparing current conditions to prior stress periods."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "signal_id": {
                        "type": "string",
                        "description": "GSS signal identifier",
                    },
                    "weeks": {
                        "type": "integer",
                        "default": 52,
                        "minimum": 4,
                        "maximum": 260,
                        "description": "Lookback period in weeks (default 52)",
                    },
                },
                "required": ["signal_id"],
            },
        ),
        Tool(
            name="get_composite_history",
            description=(
                "Returns historical composite GSS scores for trend analysis. "
                "Daily data points going back up to 5 years. "
                "Useful for understanding how current conditions compare to "
                "prior stress periods (GFC, COVID, SVB, 2022 rate shock)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "weeks": {
                        "type": "integer",
                        "default": 52,
                        "minimum": 4,
                        "maximum": 260,
                        "description": "Lookback period in weeks",
                    }
                },
                "required": [],
            },
        ),
        Tool(
            name="get_weekly_narrative",
            description=(
                "Returns the current GSS weekly narrative — a plain-language "
                "interpretation of the current market stress environment. "
                "Written for practitioner audiences. Suitable for use as "
                "context in advisor client communications."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_alert_events",
            description=(
                "Returns recent signal-level alert events: STATUS_CROSSING (upward "
                "tier transitions into ALERT or CRITICAL), FAST_MOVER (score rises "
                "10+ points in 7 days), or BOTH. Essential for understanding what "
                "changed recently — 'what moved' matters more than 'what is the score'. "
                "Default window is 7 days; max 30 days."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "default": 7,
                        "minimum": 1,
                        "maximum": 30,
                        "description": "Lookback window in days (default 7)",
                    }
                },
                "required": [],
            },
        ),
        Tool(
            name="get_compound_alerts",
            description=(
                "Returns active compound alert events — multi-signal rules that indicate "
                "systemic stress patterns. Rules: VECTOR_SURGE (2+ signals in same vector "
                "at ALERT/CRITICAL), CROSS_VECTOR_CONTAGION (3+ vectors with elevated signals), "
                "FAST_MOVER_CLUSTER (3+ fast movers in 7 days), TRANSMISSION_CHAIN "
                "(Volatility + Credit Risks both elevated), SEVERITY_MULTIPLIER "
                "(Valuation + any transmission vector elevated)."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_movers",
            description=(
                "Returns signals with recent behavioral changes — status crossings "
                "(tier transitions) and fast movers (10+ point rises in 7 days). "
                "Orthogonal to current scores — surfaces what is changing, not just "
                "what is high. Pre-computed using k15 scoring (production default)."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="get_signal_catalog",
            description=(
                "Returns the stable GSS signal schema (v1.0) — metadata for all "
                "signals including IDs, display names, vector assignments, scoring "
                "methods, and current scores. Use this to discover available signal IDs "
                "before calling get_signal_detail or get_signal_history."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


# ── Tool Call Router ─────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route tool calls to db_reader functions."""

    if name == "get_composite_stress_score":
        return _respond(db_reader.get_composite_score())

    elif name == "get_vector_status":
        slug = arguments.get("vector_slug", "")
        result = db_reader.get_vector_status(slug)
        if result is None:
            return _respond({
                "error": f"Unknown vector: {slug}",
                "valid_vectors": VECTOR_ENUM,
            })
        return _respond(result)

    elif name == "get_all_vector_scores":
        return _respond(db_reader.get_all_vector_scores())

    elif name == "get_signal_detail":
        signal_id = arguments.get("signal_id", "").upper()
        result = db_reader.get_signal_detail(signal_id)
        return _respond(result)

    elif name == "get_elevated_signals":
        threshold = arguments.get("threshold", 51)
        result = db_reader.get_elevated_signals(threshold)
        return _respond(result, note=f"Showing signals with score >= {threshold}")

    elif name == "get_signal_history":
        signal_id = arguments.get("signal_id", "").upper()
        weeks = arguments.get("weeks", 52)
        result = db_reader.get_signal_history(signal_id, weeks)
        return _respond(result)

    elif name == "get_composite_history":
        weeks = arguments.get("weeks", 52)
        result = db_reader.get_composite_history(weeks)
        return _respond(result)

    elif name == "get_weekly_narrative":
        return _respond(db_reader.get_weekly_narrative())

    elif name == "get_alert_events":
        days = arguments.get("days", 7)
        result = db_reader.get_recent_alert_events(days)
        return _respond(result, note=f"Alert events from last {days} days")

    elif name == "get_compound_alerts":
        return _respond(db_reader.get_compound_alerts())

    elif name == "get_movers":
        return _respond(db_reader.get_movers())

    elif name == "get_signal_catalog":
        return _respond(db_reader.get_signal_catalog())

    else:
        return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


# ── SSE Transport Setup ─────────────────────────────────────────────────────

sse = SseServerTransport("/messages")


def _get_api_key(request: Request) -> str | None:
    """Extract API key from Authorization: Bearer {key} header."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None


async def handle_sse(request: Request):
    """Authenticate and handle SSE connection."""
    raw_key = _get_api_key(request)

    if not raw_key:
        return JSONResponse(
            {"error": "Missing API key. Include Authorization: Bearer {key} header"},
            status_code=401,
        )

    key_record = auth.validate_key(raw_key)
    if not key_record:
        return JSONResponse(
            {"error": "Invalid or rate-limited API key"},
            status_code=401,
        )

    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(
            streams[0],
            streams[1],
            server.create_initialization_options(),
        )


async def health_check(request: Request):
    """Health endpoint for monitoring."""
    snapshot_ok = os.path.exists(config.SNAPSHOT_JSON)
    db_ok = os.path.exists(config.HISTORY_DB)

    snapshot_mtime = None
    if snapshot_ok:
        snapshot_mtime = datetime.fromtimestamp(
            os.path.getmtime(config.SNAPSHOT_JSON)
        ).isoformat()

    return JSONResponse({
        "status": "ok" if (snapshot_ok and db_ok) else "degraded",
        "server": config.SERVER_NAME,
        "version": config.SERVER_VERSION,
        "database_ok": db_ok,
        "snapshot_ok": snapshot_ok,
        "snapshot_last_modified": snapshot_mtime,
    })


# ── Starlette App ────────────────────────────────────────────────────────────

app = Starlette(
    routes=[
        Route("/health", health_check),
        Route("/sse", handle_sse),
        Mount("/messages", app=sse.handle_post_message),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")
