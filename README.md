# Financial Data API

A data pipeline built to fetch, store, and serve financial time-series data. This project simulates the data ingestion layer of a quantitative analysis platform.

It uses **FastAPI** and **SQLModel** to manage data flow between an external provider (`yfinance`) and a **PostgreSQL** database.

## Tech Stack
* **Core:** Python 3.11, FastAPI, SQLModel (SQLAlchemy + Pydantic).
* **Database:** PostgreSQL 18 (Production), SQLite (Testing).
* **Infrastructure:** Docker, Docker Compose.
* **Data Source:** `yfinance` (Yahoo Finance).

## Features
* **Ingestion Logic:** Fetches historical data for tickers (e.g., `AAPL`, `MSFT`) with "upsert" handling to prevent duplicates.
* **Incremental Sync:** The `/sync` endpoint checks the latest stored date in the DB and fetches only the missing days.
* **Async Concurrency:** Wraps blocking `yfinance` calls using `asyncio.to_thread` to keep the API responsive during external I/O.
* **Data Types:** Uses `BigInt` for volume columns to handle large-cap market data without overflow.
* Automatic Documentation: FastAPI is configured to automatically generate an OpenAPI documentation of the API served in `/docs`.

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

## Quality Assurance

### Testing Strategy
The test suite focuses on **Integration Tests** to validate database interactions.
* **Engine:** Uses **SQLite in-memory** with a `StaticPool` for speed.
* **Isolation:** Overrides the `get_session` dependency to ensure tests run against the testing in-memory database, not the production instance.

Run tests with:
```bash
pytest -v
```

### Code Formatting
The codebase adheres to strict formatting standards using **Black**.
* **Pre-commit:** Git hooks are configured to enforce formatting before commits.

## Architecture
* **Modular Monolith:** Logic is separated into `models` (schema), `repository` (DB access), and `services` (external API), decoupling the business rules from the framework.
