# Financial Data API

A data pipeline built to fetch, store, and serve financial time-series data and fundamental financial statements. This project simulates the data ingestion layer of a quantitative analysis platform.

It uses **FastAPI** and **SQLModel** to manage data flow between external providers (`yfinance`, SEC EDGAR) and a **PostgreSQL** database.

## Tech Stack
* **Core:** Python 3.11, FastAPI, SQLModel (SQLAlchemy + Pydantic).
* **Database:** PostgreSQL 18 (Production), SQLite (Testing).
* **Infrastructure:** Docker, Docker Compose.
* **Data Sources:** `yfinance` (Yahoo Finance), SEC EDGAR (fundamental financial data).

## Features
* **Price Ingestion:** Fetches historical daily prices for tickers (e.g., `AAPL`, `MSFT`) with "upsert" handling to prevent duplicates.
* **Incremental Sync:** The `/sync` endpoint checks the latest stored date in the DB and fetches only the missing days.
* **SEC EDGAR Integration:** Pulls 10-K/10-Q filings from the SEC EDGAR API and extracts standardized financial metrics (revenue, net income, EPS, etc.).
* **XBRL Taxonomy Mapping:** Translates raw XBRL tags (e.g., `us-gaap:Revenues`) into human-readable metric names using a seeded taxonomy table.
* **Async Concurrency:** Wraps blocking calls using `asyncio.to_thread` to keep the API responsive during external I/O.
* **Data Types:** Uses `BigInt` for volume columns to handle large-cap market data without overflow.
* **Automatic Documentation:** FastAPI generates OpenAPI docs served at `/docs`.

## How to Run

### Option A: Docker (Recommended)
Runs the API and Database services together.

1.  **Build and Start:**
    ```bash
    docker compose up --build
    ```
2.  **Access:**
    * API Docs: `http://localhost:8000/docs`
    * Database: Port `5432`

### Option B: Local Development
Runs the Python app locally while connecting to a Dockerized database.

1.  **Prerequisites:** Start the database service.
    ```bash
    docker compose up -d db
    ```

2.  **Set up Environment:**
    ```bash
    # Create virtual environment
    python -m venv .venv
    source .venv/bin/activate  # for unix systems

    # Install dependencies
    pip install -r requirements.txt
    ```

3.  **Configure Environment Variables:**
    Create a `.env.local` file in the root directory:
    ```ini
    DATABASE_URL=postgresql://myuser:mypassword@localhost:5432/mydb
    ```

4.  **Run the Server:**
    ```bash
    uvicorn main:app --reload
    ```

## API Usage

### Price Data (yfinance)

**Add a company** and fetch its historical daily prices:
```bash
curl -X POST http://localhost:8000/companies/AAPL
```

**Get daily prices** for a company, optionally filtered by date range:
```bash
curl http://localhost:8000/prices/AAPL
curl "http://localhost:8000/prices/AAPL?from=2024-01-01&until=2024-06-30"
```

**Sync latest prices** to fill in any missing days since the last stored date:
```bash
curl -X POST http://localhost:8000/prices/AAPL/sync
```

### Financial Statements (SEC EDGAR)

**Ingest financial data** from SEC EDGAR filings for a company that already exists in the database. This resolves the company's CIK, fetches 10-K/10-Q filings, and extracts metrics like revenue, net income, and EPS:
```bash
curl -X POST http://localhost:8000/companies/AAPL/financials
```

Response:
```json
{
  "ticker": "AAPL",
  "cik": "0000320193",
  "inserted": 42,
  "updated": 0
}
```

**Query stored financial facts** for a company, with optional filters:
```bash
# all facts
curl http://localhost:8000/companies/AAPL/financials

# filter by metric
curl "http://localhost:8000/companies/AAPL/financials?metric=revenue"

# filter by metric and period type (FY for annual, Q1-Q4 for quarterly)
curl "http://localhost:8000/companies/AAPL/financials?metric=revenue&period_type=FY"
```

### Taxonomy

**List taxonomy mappings** to see which XBRL tags map to which metrics:
```bash
curl http://localhost:8000/taxonomy

# filter by metric name
curl "http://localhost:8000/taxonomy?metric=revenue"
```

### Other Endpoints

**List all companies:**
```bash
curl http://localhost:8000/companies
```

**Delete a company** and all its associated data:
```bash
curl -X DELETE http://localhost:8000/companies/AAPL
```

## Quality Assurance

### Testing Strategy
The test suite uses **Integration Tests** to validate database interactions and API behavior.
* **Engine:** Uses **SQLite in-memory** with a `StaticPool` for speed.
* **Isolation:** Overrides the `get_session` dependency to ensure tests run against the in-memory database, not the production instance.
* **Mocking:** External services (yfinance, SEC EDGAR) are mocked at the service boundary to keep tests fast and deterministic.

Run tests with:
```bash
pytest -v
```

### Code Formatting
The codebase adheres to strict formatting standards using **Black**.
* **Pre-commit:** Git hooks are configured to enforce formatting before commits.

## Architecture
* **Modular Monolith:** Logic is separated into `models` (schema), `repository` (DB access), and `services` (external APIs and pipelines), decoupling the business rules from the framework.
* **Services layer:** `yfinance_client` handles price data, `edgar_client` handles SEC HTTP calls, `taxonomy` maps XBRL tags to metrics, and `edgar_pipeline` orchestrates the full ingestion flow.
