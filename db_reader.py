"""
GSS MCP Server - Data Reader

Reads from two sources:
  1. Exported JSON files (snapshot.json, alerts.json, gss_history.json, signal
     history files) for current state and pre-computed k-variant scores.
  2. SQLite database (gss_history.db) for historical queries not covered by JSON.

All database connections are read-only. Never write to GSS databases.

Design rationale:
  - K-factor scores (k15, k23, k3) are computed at export time by power_map.py
    in the main GSS repo. The MCP server does NOT replicate this math.
  - Alert events and compound alerts are pre-exported in alerts.json and
    snapshot.json by export_site_data.py.
  - The snapshot contains the full current state: composite, domains, signals,
    movers, safe haven regime, and 7-day alert events.

Domain-to-vector mapping:
  The GSS DB uses domain names (credit_funding, market_volatility, etc.).
  The public API uses vector slugs (credit-risks, volatility, etc.).
  This mapping matches compound_alerts.py in the main repo.
"""

import json
import os
import sqlite3
from typing import Any

import config

# ── Domain ↔ Vector Slug Mapping ────────────────────────────────────────────
# Matches DOMAIN_TO_VECTOR_SLUG in alerts/compound_alerts.py

DOMAIN_TO_VECTOR_SLUG = {
    "credit_funding": "credit-risks",
    "market_volatility": "volatility",
    "macro_conditions": "volatility",  # macro signals grouped under volatility vector
    "liquidity_plumbing": "liquidity",
    "cross_asset_contagion": "contagion",
    "valuation_fragility": "valuation",
    "safe_haven": "safe-haven",
    "banking_stress": "bank-stress",
    "private_market": "bank-stress",  # PM signals under bank stress vector
}

VALID_VECTOR_SLUGS = {
    "credit-risks", "volatility", "liquidity", "contagion",
    "valuation", "bank-stress", "safe-haven",
}

# Signals whose raw_value must not be redistributed (licensed data).
# ICE BofA indices: redistribution requires separate ICE license.
# CBOE indices: redistribution requires CBOE agreement.
RESTRICTED_RAW_VALUE = {
    "CF_CCC",       # ICE BofA CCC OAS
    "CF_HY_OAS",    # ICE BofA High Yield OAS
    "CF_IG_OAS",    # ICE BofA Investment Grade OAS
    "MV_MOVE",      # ICE BofA MOVE Index
    "MV_VIX_ABS",   # CBOE VIX
    "MV_SKEW",      # CBOE SKEW
}


def _filter_raw_value(signal_id: str, raw_value) -> any:
    """Return raw_value only if the signal's data is freely redistributable."""
    if signal_id in RESTRICTED_RAW_VALUE:
        return None
    return raw_value


# ── JSON Data Loading ────────────────────────────────────────────────────────

def _load_json(path: str) -> dict | list | None:
    """Load a JSON file, return None if missing."""
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def _snapshot() -> dict | None:
    return _load_json(config.SNAPSHOT_JSON)


# ── Composite Score ──────────────────────────────────────────────────────────

def get_composite_score() -> dict | None:
    """Latest GSS composite score with k-variant scores and alert tier."""
    snap = _snapshot()
    if not snap:
        return None

    gss = snap["gss"]
    return {
        "date": gss["date"],
        "gss_index": gss["gss_index"],
        "alert_tier": gss["alert_tier"],
        "fast_score": gss["fast_score"],
        "slow_score": gss["slow_score"],
        "acceleration": gss["acceleration"],
        "domains_active": gss["domains_active"],
        "prev_gss_index": gss.get("prev_gss_index"),
        "week_change": round(gss["gss_index"] - gss.get("prev_gss_index", gss["gss_index"]), 1),
        # k-variant scores (production default: k15)
        "gss_index_k15": gss.get("gss_index_k15"),
        "alert_tier_k15": gss.get("alert_tier_k15"),
        "gss_index_k23": gss.get("gss_index_k23"),
        "alert_tier_k23": gss.get("alert_tier_k23"),
        "gss_index_k3": gss.get("gss_index_k3"),
        "alert_tier_k3": gss.get("alert_tier_k3"),
    }


def get_composite_history(weeks: int = 52) -> list[dict]:
    """Historical composite scores from gss_history.json."""
    data = _load_json(config.HISTORY_JSON)
    if not data or "data" not in data:
        return []

    points = data["data"]
    # gss_history.json has daily points: {d, g, f, s, t}
    # d=date, g=gss_index, f=fast, s=slow, t=tier
    trading_days = weeks * 5
    recent = points[-trading_days:] if len(points) > trading_days else points

    return [
        {
            "date": p["d"],
            "gss_index": p["g"],
            "fast_score": p["f"],
            "slow_score": p["s"],
            "alert_tier": p["t"],
        }
        for p in recent
    ]


# ── Vectors (Domains) ───────────────────────────────────────────────────────

def get_all_vector_scores() -> list[dict]:
    """Current score for all vectors with k-variant scores."""
    snap = _snapshot()
    if not snap:
        return []

    results = []
    for domain, domain_data in snap["domains"].items():
        slug = DOMAIN_TO_VECTOR_SLUG.get(domain)
        if not slug:
            continue

        # Aggregate signals for this domain
        domain_signals = [
            {
                "signal_id": sid,
                "score": sig.get("sub_score_k15", sig.get("sub_score")),
                "alert_level": _tier_for_score(sig.get("sub_score_k15", sig.get("sub_score", 0))),
            }
            for sid, sig in snap["signals"].items()
            if sig.get("domain") == domain
        ]

        score = domain_data.get("score_k15", domain_data.get("score", 0))
        results.append({
            "vector_slug": slug,
            "domain": domain,
            "score": score,
            "alert_level": _tier_for_score(score),
            "signal_count": domain_data.get("signals_n", 0),
            "elevated_count": sum(1 for s in domain_signals if s["score"] and s["score"] >= 51),
            "in_gss": domain_data.get("in_gss", True),
        })

    return results


def get_vector_status(vector_slug: str) -> dict | None:
    """Detailed status for a specific vector including signal breakdown."""
    if vector_slug not in VALID_VECTOR_SLUGS:
        return None

    snap = _snapshot()
    if not snap:
        return None

    # Find domains that map to this vector slug
    matching_domains = [d for d, s in DOMAIN_TO_VECTOR_SLUG.items() if s == vector_slug]

    signals = []
    for sid, sig in snap["signals"].items():
        if sig.get("domain") in matching_domains:
            score = sig.get("sub_score_k15", sig.get("sub_score", 0))
            signals.append({
                "signal_id": sid,
                "score": score,
                "alert_level": _tier_for_score(score or 0),
                "date": sig.get("date"),
                "rarity": sig.get("level_pct"),
                "velocity": sig.get("traj_pct"),
                "raw_value": _filter_raw_value(sid, sig.get("raw_value")),
                "tier_since": sig.get("tier_since_k15", sig.get("tier_since")),
            })

    if not signals:
        return None

    scores = [s["score"] for s in signals if s["score"] is not None]
    if not scores:
        return None

    # Use domain_scores for the aggregate
    agg_score = None
    for domain in matching_domains:
        if domain in snap["domains"]:
            agg_score = snap["domains"][domain].get("score_k15", snap["domains"][domain].get("score"))
            break

    if agg_score is None:
        agg_score = sum(scores) / len(scores)

    return {
        "vector_slug": vector_slug,
        "signal_count": len(signals),
        "elevated_count": sum(1 for s in scores if s >= 51),
        "score": round(agg_score, 1),
        "max_score": max(scores),
        "alert_level": _tier_for_score(agg_score),
        "as_of_date": snap.get("as_of_date"),
        "signals": sorted(signals, key=lambda s: s["score"] or 0, reverse=True),
    }


# ── Individual Signals ───────────────────────────────────────────────────────

def get_signal_detail(signal_id: str) -> dict | None:
    """Current reading for a signal with Rarity/Velocity breakdown."""
    snap = _snapshot()
    if not snap or signal_id not in snap["signals"]:
        return None

    sig = snap["signals"][signal_id]
    score = sig.get("sub_score_k15", sig.get("sub_score", 0))

    # Get display name from signal_definitions if available
    display_name = None
    for defn in snap.get("signal_definitions", []):
        if isinstance(defn, dict) and defn.get("id") == signal_id:
            display_name = defn.get("name")
            break

    return {
        "signal_id": signal_id,
        "display_name": display_name,
        "domain": sig.get("domain"),
        "vector_slug": DOMAIN_TO_VECTOR_SLUG.get(sig.get("domain", ""), ""),
        "date": sig.get("date"),
        "score": score,
        "alert_level": _tier_for_score(score or 0),
        "rarity": sig.get("level_pct"),
        "velocity": sig.get("traj_pct"),
        "raw_value": _filter_raw_value(signal_id, sig.get("raw_value")),
        "data_quality": sig.get("data_quality"),
        "tier_since": sig.get("tier_since_k15", sig.get("tier_since")),
        # k-variant scores
        "score_k15": sig.get("sub_score_k15"),
        "score_k23": sig.get("sub_score_k23"),
        "score_k3": sig.get("sub_score_k3"),
    }


def get_signal_history(signal_id: str, weeks: int = 52) -> list[dict]:
    """Historical readings for a signal from exported JSON history files."""
    path = os.path.join(config.SIGNAL_HISTORY_DIR, f"{signal_id}.json")
    data = _load_json(path)
    if not data or "data" not in data:
        return []

    points = data["data"]
    # Signal history JSON: {d: date, v: raw_value, s: sub_score}
    # Weekly-sampled (every 5th trading day)
    weeks_of_points = weeks  # already weekly-sampled
    recent = points[-weeks_of_points:] if len(points) > weeks_of_points else points

    return [
        {
            "date": p["d"],
            "raw_value": _filter_raw_value(signal_id, p.get("v")),
            "score": p.get("s"),
            "alert_level": _tier_for_score(p.get("s", 0)),
        }
        for p in recent
    ]


def get_elevated_signals(threshold: int = 51) -> list[dict]:
    """All signals currently at or above the threshold score."""
    snap = _snapshot()
    if not snap:
        return []

    results = []
    for sid, sig in snap["signals"].items():
        score = sig.get("sub_score_k15", sig.get("sub_score", 0))
        if score is not None and score >= threshold:
            results.append({
                "signal_id": sid,
                "vector_slug": DOMAIN_TO_VECTOR_SLUG.get(sig.get("domain", ""), ""),
                "score": score,
                "alert_level": _tier_for_score(score),
                "date": sig.get("date"),
                "rarity": sig.get("level_pct"),
                "velocity": sig.get("traj_pct"),
                "raw_value": _filter_raw_value(sid, sig.get("raw_value")),
            })

    return sorted(results, key=lambda s: s["score"], reverse=True)


# ── Alert Events ─────────────────────────────────────────────────────────────

def get_recent_alert_events(days: int = 7) -> list[dict]:
    """Recent signal-level alert events (STATUS_CROSSING, FAST_MOVER, BOTH).

    Reads from snapshot.json alert_events_7d for the default 7-day window,
    or from alerts.json for the full 30-day archive.
    """
    if days <= 7:
        snap = _snapshot()
        if snap and "alert_events_7d" in snap:
            return snap["alert_events_7d"]

    alerts_data = _load_json(config.ALERTS_JSON)
    if not alerts_data:
        return []

    events = alerts_data.get("single_events", [])
    if days < 30:
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        events = [e for e in events if e.get("event_date", "") >= cutoff]

    return events


def get_compound_alerts() -> list[dict]:
    """Active compound alert events (VECTOR_SURGE, CROSS_VECTOR_CONTAGION, etc.).

    Reads from snapshot.json (active only) or alerts.json (30-day archive).
    """
    snap = _snapshot()
    if snap and "active_compound_alerts" in snap:
        active = snap["active_compound_alerts"]
        if active:
            return active

    # Fall back to alerts.json
    alerts_data = _load_json(config.ALERTS_JSON)
    if alerts_data:
        return alerts_data.get("compound_events", [])

    return []


def get_movers() -> list[dict]:
    """Signals with recent behavioral changes — status crossings and fast movers.

    Pre-computed by export_site_data.py using k15 scoring (production default).
    """
    snap = _snapshot()
    if not snap:
        return []

    return snap.get("movers_k15", snap.get("movers", []))


# ── Safe Haven ───────────────────────────────────────────────────────────────

def get_safe_haven_status() -> dict | None:
    """Current safe haven regime with correlation readings.

    Reads from the shd_regime_log data in snapshot.json, which includes:
    - Regime classification (LIQUIDATION_CRISIS, BOND_HEDGE_FAILURE, etc.)
    - Gold/SPY, Treasury/SPY, DXY/SPY correlations
    - Confidence score and fiscal modifier
    """
    snap = _snapshot()
    if not snap or "safe_haven_regime" not in snap:
        return None

    regime = snap["safe_haven_regime"]
    return {
        "date": regime.get("date"),
        "regime": regime.get("regime"),
        "regime_score": regime.get("vector_score"),
        "confidence": regime.get("confidence"),
        "correlations": regime.get("correlations", {}),
        "correlations_60d": regime.get("correlations_60d", {}),
        "spy_20d_return": regime.get("spy_20d_return"),
        "alert_level": _tier_for_score(regime.get("vector_score", 0)),
    }


# ── Narrative ────────────────────────────────────────────────────────────────

def get_weekly_narrative() -> dict | None:
    """Latest weekly narrative from the exported data."""
    data = _load_json(config.NARRATIVE_JSON)
    if data:
        return data

    return {
        "narrative": None,
        "note": "Narrative not available — check export_site_data.py has run",
    }


# ── Signal Catalog ───────────────────────────────────────────────────────────

def get_signal_catalog() -> dict | None:
    """Stable signal schema (v1.0) — all signal metadata for tool discovery."""
    return _load_json(config.SIGNALS_INDEX_JSON)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _tier_for_score(score: float) -> str:
    """Map a 0-100 score to an alert tier name.
    Matches scoring/power_map.py tier_for_score() and constants.js tierForScore().
    """
    if score is None:
        return "BASELINE"
    if score >= 76:
        return "CRITICAL"
    if score >= 51:
        return "ALERT"
    if score >= 26:
        return "WATCH"
    return "BASELINE"
