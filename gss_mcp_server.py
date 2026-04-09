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
import contextvars
import json
import logging
import os
import time
from datetime import datetime

import httpx
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

logger = logging.getLogger("gss_mcp")

# Context variable to carry auth info from SSE handler into tool calls
_current_key = contextvars.ContextVar("current_key", default=None)

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


# ── Usage Logging ──────────────────────────────────────────────────────────

async def _send_ga4_event(tool_name: str, arguments: dict, response_ms: int,
                          key_label: str, key_tier: str) -> None:
    """Fire a GA4 Measurement Protocol event (async, fire-and-forget)."""
    if not config.GA4_MEASUREMENT_ID or not config.GA4_API_SECRET:
        return
    try:
        params = {
            "tool_name": tool_name,
            "key_label": key_label,
            "key_tier": key_tier,
            "response_ms": response_ms,
        }
        # Include the most useful argument per tool (signal_id, vector_slug, etc.)
        for arg_key in ("signal_id", "vector_slug", "threshold", "days", "weeks"):
            if arg_key in arguments:
                params[arg_key] = str(arguments[arg_key])

        url = (
            f"https://www.google-analytics.com/mp/collect"
            f"?measurement_id={config.GA4_MEASUREMENT_ID}"
            f"&api_secret={config.GA4_API_SECRET}"
        )
        payload = {
            "client_id": key_label or "unknown",
            "events": [{"name": "mcp_tool_call", "params": params}],
        }
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, timeout=5.0)
    except Exception as e:
        logger.debug("GA4 send failed: %s", e)


def _log_tool_call(tool_name: str, arguments: dict, response_ms: int) -> None:
    """Log tool call to local DB + fire GA4 event async."""
    key_record = _current_key.get()
    key_id = key_record["key_id"] if key_record else None
    key_label = key_record.get("key_label", "unknown") if key_record else "unknown"
    key_tier = key_record.get("tier", "unknown") if key_record else "unknown"

    # Local DB log
    if key_id is not None:
        try:
            auth.log_tool_call(key_id, tool_name, response_ms)
        except Exception as e:
            logger.warning("Failed to write request_log: %s", e)

    logger.info("tool=%s key=%s tier=%s args=%s ms=%d",
                tool_name, key_label, key_tier, arguments, response_ms)

    # GA4 (fire-and-forget)
    asyncio.ensure_future(
        _send_ga4_event(tool_name, arguments, response_ms, key_label, key_tier)
    )


# ── Tool Call Router ─────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route tool calls to db_reader functions."""
    t0 = time.monotonic()

    if name == "get_vector_status":
        slug = arguments.get("vector_slug", "")
        data = db_reader.get_vector_status(slug)
        if data is None:
            result = _respond({
                "error": f"Unknown vector: {slug}",
                "valid_vectors": VECTOR_ENUM,
            })
        else:
            result = _respond(data)

    elif name == "get_all_vector_scores":
        result = _respond(db_reader.get_all_vector_scores())

    elif name == "get_signal_detail":
        signal_id = arguments.get("signal_id", "").upper()
        result = _respond(db_reader.get_signal_detail(signal_id))

    elif name == "get_elevated_signals":
        threshold = arguments.get("threshold", 51)
        result = _respond(db_reader.get_elevated_signals(threshold),
                          note=f"Showing signals with score >= {threshold}")

    elif name == "get_signal_history":
        signal_id = arguments.get("signal_id", "").upper()
        weeks = arguments.get("weeks", 52)
        result = _respond(db_reader.get_signal_history(signal_id, weeks))

    elif name == "get_weekly_narrative":
        result = _respond(db_reader.get_weekly_narrative())

    elif name == "get_alert_events":
        days = arguments.get("days", 7)
        result = _respond(db_reader.get_recent_alert_events(days),
                          note=f"Alert events from last {days} days")

    elif name == "get_compound_alerts":
        result = _respond(db_reader.get_compound_alerts())

    elif name == "get_movers":
        result = _respond(db_reader.get_movers())

    elif name == "get_signal_catalog":
        result = _respond(db_reader.get_signal_catalog())

    else:
        result = [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]

    response_ms = int((time.monotonic() - t0) * 1000)
    _log_tool_call(name, arguments, response_ms)
    return result


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
        logger.info("auth_rejected key=%s", raw_key[:8] + "...")
        return JSONResponse(
            {"error": "Invalid or rate-limited API key"},
            status_code=401,
        )

    _current_key.set(key_record)
    logger.info("session_start key=%s tier=%s", key_record["key_label"], key_record["tier"])

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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")
