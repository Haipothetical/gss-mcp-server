"""
GSS MCP Server - API Key Management CLI

Usage:
    python manage_keys.py init                                    # Create tables
    python manage_keys.py create --label "TradingAgents" --tier free
    python manage_keys.py list
    python manage_keys.py deactivate --label "TradingAgents"
"""

import argparse
import sqlite3
import sys

import auth
from config import API_KEYS_DB


def cmd_init():
    auth.init_db()
    print(f"Initialized API keys database at {API_KEYS_DB}")


def cmd_create(label: str, tier: str):
    raw_key = auth.create_key(label, tier)
    print(f"Created API key for '{label}' (tier: {tier})")
    print(f"Key: {raw_key}")
    print("Store this key securely — it cannot be retrieved after this.")


def cmd_list():
    conn = sqlite3.connect(API_KEYS_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT key_id, key_label, tier, daily_limit, requests_today, "
        "last_reset_date, active, created_at FROM api_keys ORDER BY key_id"
    ).fetchall()
    conn.close()

    if not rows:
        print("No API keys found.")
        return

    print(f"{'ID':<4} {'Label':<25} {'Tier':<14} {'Limit':<7} {'Used':<6} {'Active':<7} {'Created'}")
    print("-" * 90)
    for r in rows:
        active = "yes" if r["active"] else "no"
        print(
            f"{r['key_id']:<4} {r['key_label']:<25} {r['tier']:<14} "
            f"{r['daily_limit']:<7} {r['requests_today']:<6} {active:<7} {r['created_at']}"
        )


def cmd_deactivate(label: str):
    conn = sqlite3.connect(API_KEYS_DB)
    cursor = conn.execute(
        "UPDATE api_keys SET active = 0 WHERE key_label = ? AND active = 1",
        (label,),
    )
    conn.commit()
    if cursor.rowcount:
        print(f"Deactivated key '{label}'")
    else:
        print(f"No active key found with label '{label}'")
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="GSS MCP Server — API Key Management")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Initialize API keys database")

    create_p = sub.add_parser("create", help="Create a new API key")
    create_p.add_argument("--label", required=True, help="Human-readable key label")
    create_p.add_argument(
        "--tier", default="free", choices=["free", "professional", "enterprise"],
        help="Rate limit tier (default: free)",
    )

    sub.add_parser("list", help="List all API keys")

    deact_p = sub.add_parser("deactivate", help="Deactivate an API key")
    deact_p.add_argument("--label", required=True, help="Key label to deactivate")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init()
    elif args.command == "create":
        cmd_create(args.label, args.tier)
    elif args.command == "list":
        cmd_list()
    elif args.command == "deactivate":
        cmd_deactivate(args.label)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
