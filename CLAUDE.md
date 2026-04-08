# GSS MCP Server

## Overview
Read-only MCP server exposing Grey Swan Signals market stress intelligence as tools for AI agents. No scoring logic — serves pre-computed outputs from the main GSS pipeline.

## Architecture
- **Data sources**: Reads from exported JSON (snapshot.json, alerts.json, gss_history.json) and SQLite (gss_history.db, read-only)
- **Transport**: HTTP/SSE via Starlette + Uvicorn
- **Auth**: Bearer token API keys (SHA-256 hashed in gss_api_keys.db)
- **Exposure**: Cloudflare Tunnel at mcp.greyswansignals.com

## Key Design Decisions

### JSON-first data access
The server reads pre-computed data from `export_site_data.py` output rather than replicating k-factor scoring from raw DB columns. This avoids methodology leakage and ensures MCP scores exactly match the website.

### Domain-to-Vector mapping
The DB uses domain names (credit_funding, market_volatility). The public API uses vector slugs (credit-risks, volatility). Mapping matches `DOMAIN_TO_VECTOR_SLUG` in `alerts/compound_alerts.py`.

### Scoring mode
All scores use k15 (k=1.5 power transform, production default). K23 and k3 variants included in signal detail responses.

### Alert tools
Three alert-related tools surface the alerts backend: `get_alert_events` (signal-level), `get_compound_alerts` (multi-signal rules), `get_movers` (behavioral changes).

## Files
- `gss_mcp_server.py` — Main server, tool definitions, SSE transport
- `db_reader.py` — Data access layer (JSON + SQLite)
- `auth.py` — API key validation and rate limiting
- `config.py` — Environment-based configuration
- `manage_keys.py` — CLI for API key management

## Running
```bash
python gss_mcp_server.py  # Starts on port 8765
```

## Do Not
- Write to any GSS database
- Expose k-factor values, signal weights, or scoring methodology
- Include raw FRED/ICE/BofA data values — scores only
- Commit .env, *.db files
- Add code to the main Haipothetical/GSS repo
