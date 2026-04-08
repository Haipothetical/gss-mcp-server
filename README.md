# GSS MCP Server

Model Context Protocol server exposing [Grey Swan Signals](https://greyswansignals.com) market stress intelligence as callable tools for AI agents.

GSS scores 30+ financial signals across 6 risk vectors to produce a composite market stress index (0-100). Data updates weekly. This server provides read-only access to pre-computed scored outputs — no raw data, no methodology.

## Tools

| Tool | Description | Key Output |
|---|---|---|
| `get_composite_stress_score` | Current market stress summary | score, alert_tier, week_change, k-variants |
| `get_vector_status` | Single vector reading | score, elevated_count, signals[] |
| `get_all_vector_scores` | All vectors at once | array of vector readings |
| `get_signal_detail` | Individual signal | score, rarity, velocity, k-variants |
| `get_elevated_signals` | Signals above threshold | filtered signal list |
| `get_signal_history` | Historical signal readings | weekly-sampled time series |
| `get_composite_history` | Historical composite | daily time series |
| `get_weekly_narrative` | Plain-language interpretation | narrative text |
| `get_alert_events` | Recent signal-level alerts | STATUS_CROSSING, FAST_MOVER events |
| `get_compound_alerts` | Multi-signal stress patterns | VECTOR_SURGE, CONTAGION, etc. |
| `get_movers` | Signals with recent changes | crossings + fast movers |
| `get_signal_catalog` | Signal metadata schema (v1.0) | IDs, names, vectors, methods |

## Risk Vectors

| Vector | Slug | Signals |
|---|---|---|
| Credit Risks | `credit-risks` | HY OAS, CCC Spread, IG/HY Deltas, SOFR-OIS, CP Spread |
| Volatility | `volatility` | VIX, SKEW, MOVE, Oil Shock, Yield Curve |
| Liquidity | `liquidity` | Reverse Repo, Reserves, Repo Fails, SOFR |
| Contagion | `contagion` | Yen Carry, Nikkei Drawdown, EU Sovereign, JGB-UST, CFTC JPY |
| Valuation | `valuation` | CAPE, ERP, Breadth, Inflation Expectations |
| Bank Stress | `bank-stress` | KBW/KRE, BKX Relative, BKX-KRE Divergence, Private Market |

## Alert Events

The alert system surfaces **what changed** — often more valuable than static scores:

- **STATUS_CROSSING**: Signal transitions upward into ALERT or CRITICAL tier
- **FAST_MOVER**: Signal score rises 10+ points in 7 days
- **VECTOR_SURGE**: 2+ signals in same vector at ALERT/CRITICAL
- **CROSS_VECTOR_CONTAGION**: 3+ vectors with elevated signals
- **FAST_MOVER_CLUSTER**: 3+ fast movers across any vectors in 7 days
- **TRANSMISSION_CHAIN**: Volatility + Credit Risks both elevated
- **SEVERITY_MULTIPLIER**: Valuation + any transmission vector elevated

## Authentication

Include a Bearer token in the Authorization header:

```
Authorization: Bearer gss_your_api_key_here
```

Request a key at greyswansignals@gmail.com with your intended use case.

### Tiers

| Tier | Daily Calls | Use Case |
|---|---|---|
| Free | 100 | Development and evaluation |
| Professional | 1,000 | Production integration |
| Enterprise | 10,000 | High-volume or multi-tenant |

## Connection

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "gss": {
      "url": "https://mcp.greyswansignals.com/sse",
      "headers": {"Authorization": "Bearer gss_your_key_here"}
    }
  }
}
```

### Anthropic API (Remote MCP)

```python
import anthropic

client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=1024,
    mcp_servers=[{
        "type": "url",
        "url": "https://mcp.greyswansignals.com/sse",
        "name": "gss",
        "authorization_token": "gss_your_key_here"
    }],
    messages=[{
        "role": "user",
        "content": "What is the current market stress reading and which vectors are elevated?"
    }]
)
```

## Data Freshness

Every response includes a `meta` object:

```json
{
  "data": { ... },
  "meta": {
    "last_updated": "2026-04-06",
    "update_cadence": "weekly",
    "scoring_mode": "k15",
    "source": "Grey Swan Signals — greyswansignals.com"
  }
}
```

GSS data updates weekly. For intraday applications, GSS provides macro regime context, not real-time signals.

## Scoring

Scores use the **k15** power-mapped scoring mode (production default):
- **Rarity (70%)**: Percentile rank of current level within full historical distribution
- **Velocity (30%)**: Percentile rank of 12-week rate of change
- **k=1.5 power transform**: Compresses mid-range percentiles, preserves extremes

Tier thresholds: BASELINE (0-25), WATCH (26-50), ALERT (51-75), CRITICAL (76-100).

## Self-Hosting

```bash
# Install dependencies
pip install -r requirements.txt

# Initialize API keys database
python manage_keys.py init
python manage_keys.py create --label "local-test" --tier free

# Configure
cp .env.example .env
# Edit .env with paths to your GSS data

# Start server
python gss_mcp_server.py
# → INFO: Uvicorn running on http://0.0.0.0:8765

# Test
curl http://localhost:8765/health
```

## Health Check

```
GET /health
```

Returns server status, database availability, and last data update timestamp.

---

*Grey Swan Signals — [greyswansignals.com](https://greyswansignals.com)*
