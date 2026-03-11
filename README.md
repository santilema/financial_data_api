# FinDataAI

A token-efficient financial data API designed for LLM agent consumption. Combines SEC EDGAR
fundamental data with yfinance price history and exposes it through a clean REST interface
with agent-optimized response formats.

Interactive docs: `http://localhost:8000/docs`

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| Framework | FastAPI + SQLModel (SQLAlchemy + Pydantic) |
| Database | PostgreSQL (production), SQLite (testing) |
| Data Sources | SEC EDGAR, yfinance |
| Infrastructure | Docker, Docker Compose |

---

## Quick Start

### Docker (Recommended)

```bash
docker compose up --build
# API: http://localhost:8000
# Docs: http://localhost:8000/docs
```

### Local Development

```bash
# Start the database
docker compose up -d db

# Set up environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure
echo "DATABASE_URL=postgresql://myuser:mypassword@localhost:5432/mydb" > .env.local

# Run
uvicorn main:app --reload
```

---

## Endpoint Reference

| Method | Path | Description | Auth |
|---|---|---|---|
| POST | `/companies/{ticker}` | Register company + fetch price history | - |
| GET | `/companies` | List all registered companies | - |
| GET | `/companies/{ticker}` | Company profile with latest financials | - |
| DELETE | `/companies/{ticker}` | Remove company and all its data | - |
| GET | `/prices/{ticker}` | Daily OHLCV prices, optional date range | - |
| POST | `/prices/{ticker}/sync` | Incremental price sync (new days only) | - |
| DELETE | `/prices/{ticker}` | Alias for DELETE /companies/{ticker} | - |
| POST | `/companies/{ticker}/financials` | Trigger SEC EDGAR ingestion | - |
| GET | `/companies/{ticker}/financials` | Stored financial facts by period | - |
| GET | `/companies/{ticker}/ratios` | Computed ratios for latest FY | - |
| GET | `/companies/{ticker}/trend` | Multi-year trend data with computed ratios | - |
| GET | `/search` | Search by ticker or name | - |
| GET | `/compare` | Side-by-side metric matrix for multiple tickers | - |
| GET | `/taxonomy` | XBRL tag to metric name mappings | - |
| POST | `/admin/ingest/batch` | Batch EDGAR ingestion (background job) | X-Admin-Key |
| GET | `/admin/ingest/status/{job_id}` | Poll batch ingestion job status | X-Admin-Key |

---

## Key Concepts

### `?format=` parameter

All financial endpoints accept `?format=minimal|standard|verbose`:

- `minimal` (default) - short keys optimized for LLM context efficiency (`rev`, `ni`, `gm`)
- `standard` - full descriptive names (`revenue`, `net_income`, `gross_margin`)
- `verbose` - full names plus units and descriptions

### `?fields=` and `?metrics=` parameters

Comma-separated minimal-key lists to select only the columns you need:

```
GET /companies/AAPL/ratios?fields=gm,pe,de
GET /companies/AAPL/trend?metrics=rev,ni,gm
GET /compare?tickers=AAPL,MSFT&metrics=rev,ni,pe
```

### `_meta` freshness object

Every period-grouped response includes a `_meta` object:

```json
{
  "_meta": {
    "src": "edgar",
    "age_days": 42,
    "filed": "2024-01-15",
    "fy": 2023
  }
}
```

### Error schema

All errors follow a consistent shape:

```json
{
  "error": "not_found",
  "message": "Company ZZZZ not found.",
  "ticker": "ZZZZ"
}
```

### Search availability fields

`GET /search` returns `has_financials` and `latest_fy` so agents can distinguish companies
with ingested EDGAR data from shell entries:

```json
[
  {"ticker": "AAPL", "name": "Apple Inc.", "sector": "Technology",
   "has_financials": true, "latest_fy": 2023},
  {"ticker": "NEWCO", "name": "New Corp", "sector": null,
   "has_financials": false, "latest_fy": null}
]
```

---

## Example Responses

### `GET /search?q=apple`

```json
[
  {
    "ticker": "AAPL",
    "name": "Apple Inc.",
    "sector": "Technology",
    "has_financials": true,
    "latest_fy": 2023
  }
]
```

### `GET /companies/AAPL/ratios?format=minimal`

```json
{
  "ticker": "AAPL",
  "gm": 0.441,
  "om": 0.297,
  "nm": 0.253,
  "de": 1.79,
  "roe": 1.56,
  "pe": 28.4,
  "pb": 42.1,
  "_meta": {"src": "edgar+yfinance", "age_days": 60, "filed": "2023-11-03", "fy": 2023, "price_date": "2024-03-01"}
}
```

### `GET /companies/AAPL/trend?metrics=rev,ni,gm&periods=3`

```json
{
  "ticker": "AAPL",
  "metrics": ["rev", "ni", "gm"],
  "periods": [
    {"fy": 2021, "rev": 365817000000, "ni": 94680000000, "gm": 0.418},
    {"fy": 2022, "rev": 394328000000, "ni": 99803000000, "gm": 0.433},
    {"fy": 2023, "rev": 383285000000, "ni": 96995000000, "gm": 0.441}
  ],
  "_meta": {"coverage": {"from": 2021, "to": 2023}, "periods_available": 3}
}
```

### `GET /compare?tickers=AAPL,MSFT&metrics=rev,ni,gm`

```json
{
  "metrics": ["rev", "ni", "gm"],
  "data": {
    "AAPL": {"rev": 383285000000, "ni": 96995000000, "gm": 0.441},
    "MSFT": {"rev": 211915000000, "ni": 72361000000, "gm": 0.699}
  },
  "_meta": {"fy_used": {"AAPL": 2023, "MSFT": 2023}}
}
```

---

## Admin Endpoints

Batch ingestion is protected by the `X-Admin-Key` header (default: `dev` in development).

### Batch ingest workflow

```bash
# Start a background ingestion job for all registered companies
curl -X POST "http://localhost:8000/admin/ingest/batch" \
  -H "X-Admin-Key: dev"

# Or for specific tickers
curl -X POST "http://localhost:8000/admin/ingest/batch?tickers=AAPL,MSFT,GOOG" \
  -H "X-Admin-Key: dev"

# Response
# {"job_id": "abc-123", "total": 3, "status": "pending"}

# Poll until completed
curl "http://localhost:8000/admin/ingest/status/abc-123" \
  -H "X-Admin-Key: dev"
```

Set `ADMIN_KEY` environment variable in production to override the default.

---

## Testing

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

The test suite uses:
- **SQLite in-memory** database with `StaticPool` for speed and isolation
- **Dependency injection override** to route tests through the in-memory DB
- **`unittest.mock.patch`** for external services (yfinance, SEC EDGAR)
- **Direct `db.add()`** to seed test data without going through API endpoints

---

## Architecture

```
main.py            FastAPI endpoints - request validation, routing, response shaping
admin.py           Admin router - batch ingestion with background tasks
repository.py      Database access layer - all SQL queries in one place
schemas.py         Response transformers - format/field filtering, ratio/trend computation
models.py          SQLModel table definitions
services/
  ratios.py        RatioInputs dataclass + compute_ratios function
  edgar_pipeline.py  Orchestrates EDGAR ingestion: CIK lookup, fact extraction, upsert
  edgar_client.py  Low-level SEC EDGAR HTTP client
  yfinance_client.py  Yahoo Finance price fetcher
  taxonomy.py      XBRL tag seeding logic
database.py        Engine and session factory
```

Data flows: `endpoint -> repository -> DB` for reads, `endpoint -> service -> repository -> DB`
for ingestion. Schema transforms happen in `schemas.py` after data is fetched, keeping the
repository layer format-agnostic.
