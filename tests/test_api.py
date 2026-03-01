from unittest.mock import patch, AsyncMock
from datetime import date, timedelta
from models import Company, DailyPrice, FinancialFact
from services.yfinance_client import YahooFinanceError
from services.edgar_client import EdgarClientError

# --- Create companies ---


def test_add_company_success(client):
    ticker = "AAPL"
    mock_company = Company(ticker=ticker, name="Apple Inc.")

    mock_prices = [
        DailyPrice(
            company_id=None,  # db assigns this
            date=date(2025, 1, 1),
            open=100.0,
            high=110.0,
            low=90.0,
            close=105.0,
            volume=1000,
        )
    ]

    # Patching the sync function directly
    with patch("services.yfinance_client._fetch_data_sync") as mock_fetch:
        mock_fetch.return_value = (mock_company, mock_prices)
        response = client.post(f"/companies/{ticker}")

    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == ticker
    assert data["id"] is not None
    # ensure company is listed
    companies_response = client.get("/companies")
    assert len(companies_response.json()) == 1
    assert companies_response.json()[0]["ticker"] == ticker

    # ensure price actually persisted
    price_response = client.get(f"/prices/{ticker}")
    assert len(price_response.json()) == 1


def test_add_duplicate_company(client):
    ticker = "MSFT"

    # first attempt
    with patch("services.yfinance_client._fetch_data_sync") as mock_fetch:
        mock_fetch.return_value = (Company(ticker=ticker, name="Micro"), [])
        client.post(f"/companies/{ticker}")

    # second attempt should fail
    response = client.post(f"/companies/{ticker}")

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


def test_add_company_yfinance_failure(client):
    ticker = "INVALID"

    with patch("services.yfinance_client._fetch_data_sync") as mock_fetch:
        mock_fetch.side_effect = YahooFinanceError("Ticker not found")
        response = client.post(f"/companies/{ticker}")

    assert response.status_code == 400
    assert "Ticker not found" in response.json()["detail"]


# --- Retrieve Prices ---


def test_get_prices_not_found(client):
    response = client.get("/prices/INVALID")
    assert response.status_code == 404


def test_get_prices_default_full_history(client):
    ticker = "AMZN"
    first = date(2024, 1, 1)
    second = date(2024, 1, 2)

    with patch("services.yfinance_client._fetch_data_sync") as mock_fetch:
        mock_fetch.return_value = (
            Company(ticker=ticker, name="Amazon"),
            [
                DailyPrice(
                    company_id=None,
                    date=first,
                    open=1,
                    high=1,
                    low=1,
                    close=1,
                    volume=100,
                ),
                DailyPrice(
                    company_id=None,
                    date=second,
                    open=2,
                    high=2,
                    low=2,
                    close=2,
                    volume=200,
                ),
            ],
        )
        client.post(f"/companies/{ticker}")

    response = client.get(f"/prices/{ticker}")
    assert response.status_code == 200
    data = response.json()
    assert [p["date"] for p in data] == [
        first.isoformat(),
        second.isoformat(),
    ]


def test_get_prices_filtered_range(client):
    ticker = "META"
    dates = [date(2023, 1, 1), date(2023, 1, 2), date(2023, 1, 3)]

    with patch("services.yfinance_client._fetch_data_sync") as mock_fetch:
        mock_fetch.return_value = (
            Company(ticker=ticker, name="Meta"),
            [
                DailyPrice(
                    company_id=None,
                    date=d,
                    open=i,
                    high=i,
                    low=i,
                    close=i,
                    volume=10 * i,
                )
                for i, d in enumerate(dates, start=1)
            ],
        )
        client.post(f"/companies/{ticker}")

    response = client.get(f"/prices/{ticker}?from=2023-01-02&until=2023-01-03")
    assert response.status_code == 200
    data = response.json()
    assert [p["date"] for p in data] == ["2023-01-02", "2023-01-03"]

    response_single = client.get(f"/prices/{ticker}?from=2023-01-03&until=2023-01-03")
    assert [p["date"] for p in response_single.json()] == ["2023-01-03"]


def test_get_prices_invalid_range(client):
    ticker = "ORCL"
    with patch("services.yfinance_client._fetch_data_sync") as mock_fetch:
        mock_fetch.return_value = (
            Company(ticker=ticker, name="Oracle"),
            [
                DailyPrice(
                    company_id=None,
                    date=date(2022, 1, 1),
                    open=1,
                    high=1,
                    low=1,
                    close=1,
                    volume=1,
                )
            ],
        )
        client.post(f"/companies/{ticker}")

    response = client.get(f"/prices/{ticker}?from=2022-02-01&until=2022-01-01")
    assert response.status_code == 400
    assert "must be on or before" in response.json()["detail"]


# --- Sync Logic ---


def test_sync_prices_success(client):
    ticker = "GOOG"
    old_date = date.today() - timedelta(days=5)

    # 1. fill db with old data
    with patch("services.yfinance_client._fetch_data_sync") as mock_fetch_init:
        mock_fetch_init.return_value = (
            Company(ticker=ticker, name="Google"),
            [
                DailyPrice(
                    company_id=None,
                    date=old_date,
                    open=1,
                    high=1,
                    low=1,
                    close=1,
                    volume=100,
                )
            ],
        )
        client.post(f"/companies/{ticker}")

    # 2. Sync new data (simulate 1 new day)
    new_price = DailyPrice(
        company_id=None,
        date=date.today(),
        open=2,
        high=2,
        low=2,
        close=2,
        volume=200,
    )

    with patch("services.yfinance_client._fetch_data_since_sync") as mock_sync:
        mock_sync.return_value = [new_price]
        response = client.post(f"/prices/{ticker}/sync")

        # Verify requested data starts from the day after last record
        expected_start = old_date + timedelta(days=1)
        assert mock_sync.call_args[0][1] == expected_start

    assert response.status_code == 200
    assert "1 new records" in response.json()["message"]


def test_sync_prices_already_up_to_date(client):
    ticker = "NVDA"
    yesterday = date.today() - timedelta(days=1)

    # fill with fresh data
    with patch("services.yfinance_client._fetch_data_sync") as mock_fetch:
        mock_fetch.return_value = (
            Company(ticker=ticker, name="Nvidia"),
            [
                DailyPrice(
                    company_id=None,
                    date=yesterday,
                    open=1,
                    high=1,
                    low=1,
                    close=1,
                    volume=1,
                )
            ],
        )
        client.post(f"/companies/{ticker}")

    # sync should hit the guard clause and avoid external calls
    with patch("services.yfinance_client._fetch_data_since_sync") as mock_sync:
        response = client.post(f"/prices/{ticker}/sync")
        mock_sync.assert_not_called()

    assert response.status_code == 200
    assert "already up-to-date" in response.json()["message"]


def test_sync_prices_ticker_not_found(client):
    response = client.post("/prices/INVALID/sync")
    assert response.status_code == 404


# --- Delete Companies ---


def test_delete_company_removes_prices(client):
    ticker = "TSLA"
    price_date = date(2024, 1, 1)

    with patch("services.yfinance_client._fetch_data_sync") as mock_fetch:
        mock_fetch.return_value = (
            Company(ticker=ticker, name="Tesla"),
            [
                DailyPrice(
                    company_id=None,
                    date=price_date,
                    open=1,
                    high=1,
                    low=1,
                    close=1,
                    volume=10,
                )
            ],
        )
        client.post(f"/companies/{ticker}")

    response = client.delete(f"/companies/{ticker}")
    assert response.status_code == 200
    data = response.json()
    assert data["prices_deleted"] == 1
    assert data["company_deleted"] == 1
    assert ticker in data["message"]

    # company and prices should be gone
    price_response = client.get(f"/prices/{ticker}")
    assert price_response.status_code == 404


def test_delete_company_not_found_does_not_touch_other_data(client):
    existing_ticker = "IBM"
    with patch("services.yfinance_client._fetch_data_sync") as mock_fetch:
        mock_fetch.return_value = (
            Company(ticker=existing_ticker, name="IBM"),
            [
                DailyPrice(
                    company_id=None,
                    date=date(2023, 1, 1),
                    open=1,
                    high=1,
                    low=1,
                    close=1,
                    volume=100,
                )
            ],
        )
        client.post(f"/companies/{existing_ticker}")

    response = client.delete("/companies/UNKNOWN")
    assert response.status_code == 404

    # existing ticker data should remain untouched
    existing_prices = client.get(f"/prices/{existing_ticker}")
    assert existing_prices.status_code == 200
    assert len(existing_prices.json()) == 1


# --- Financials (EDGAR) ---


def _create_company(client, ticker, name):
    """Helper to create a company via the API."""
    with patch("services.yfinance_client._fetch_data_sync") as mock_fetch:
        mock_fetch.return_value = (Company(ticker=ticker, name=name), [])
        client.post(f"/companies/{ticker}")


def test_ingest_financials_success(client):
    ticker = "AAPL"
    _create_company(client, ticker, "Apple Inc.")

    summary = {"ticker": ticker, "cik": "0000320193", "inserted": 5, "updated": 0}
    with patch("main.ingest_company_financials", new_callable=AsyncMock) as mock_ingest:
        mock_ingest.return_value = summary
        response = client.post(f"/companies/{ticker}/financials")

    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == ticker
    assert data["cik"] == "0000320193"
    assert data["inserted"] == 5


def test_ingest_financials_company_not_found(client):
    response = client.post("/companies/UNKNOWN/financials")
    assert response.status_code == 404


def test_ingest_financials_edgar_error(client):
    ticker = "MSFT"
    _create_company(client, ticker, "Microsoft")

    with patch("main.ingest_company_financials", new_callable=AsyncMock) as mock_ingest:
        mock_ingest.side_effect = EdgarClientError("EDGAR API unavailable")
        response = client.post(f"/companies/{ticker}/financials")

    assert response.status_code == 400
    assert "EDGAR API unavailable" in response.json()["detail"]


def test_get_financials_success(client, engine):
    ticker = "AMZN"
    _create_company(client, ticker, "Amazon")

    # insert facts directly into the db
    from sqlmodel import Session

    with Session(engine) as db:
        from repository import get_company_by_ticker

        company = get_company_by_ticker(db, ticker)
        fact = FinancialFact(
            company_id=company.id,
            metric="revenue",
            value=100_000_000,
            unit="USD",
            end_date=date(2024, 12, 31),
            period_type="FY",
        )
        db.add(fact)
        db.commit()

    response = client.get(f"/companies/{ticker}/financials?format=verbose")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["period_type"] == "FY"
    assert len(data[0]["metrics"]) == 1
    assert data[0]["metrics"][0]["metric"] == "revenue"


def test_get_financials_with_filters(client, engine):
    ticker = "META"
    _create_company(client, ticker, "Meta")

    from sqlmodel import Session

    with Session(engine) as db:
        from repository import get_company_by_ticker

        company = get_company_by_ticker(db, ticker)
        facts = [
            FinancialFact(
                company_id=company.id,
                metric="revenue",
                value=100_000_000,
                unit="USD",
                end_date=date(2024, 12, 31),
                period_type="FY",
            ),
            FinancialFact(
                company_id=company.id,
                metric="net_income",
                value=20_000_000,
                unit="USD",
                end_date=date(2024, 12, 31),
                period_type="FY",
            ),
            FinancialFact(
                company_id=company.id,
                metric="revenue",
                value=25_000_000,
                unit="USD",
                end_date=date(2024, 3, 31),
                period_type="Q1",
            ),
        ]
        for f in facts:
            db.add(f)
        db.commit()

    # filter by metric only
    response = client.get(f"/companies/{ticker}/financials?metric=revenue&format=verbose")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert all(d["metrics"][0]["metric"] == "revenue" for d in data)

    # filter by metric and period_type
    response = client.get(
        f"/companies/{ticker}/financials?metric=revenue&period_type=FY&format=verbose"
    )
    data = response.json()
    assert len(data) == 1
    assert data[0]["period_type"] == "FY"


def test_get_financials_company_not_found(client):
    response = client.get("/companies/UNKNOWN/financials")
    assert response.status_code == 404


def test_get_taxonomy(client):
    response = client.get("/taxonomy")
    assert response.status_code == 200
    data = response.json()
    # taxonomy is seeded during lifespan, so there should be mappings
    assert len(data) > 0
    assert "xbrl_tag" in data[0]
    assert "metric" in data[0]


def test_get_taxonomy_filtered(client):
    response = client.get("/taxonomy?metric=revenue")
    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0
    assert all(d["metric"] == "revenue" for d in data)


def test_get_financials_format_minimal(client, engine):
    ticker = "AAPL"
    _create_company(client, ticker, "Apple")

    from sqlmodel import Session

    with Session(engine) as db:
        from repository import get_company_by_ticker

        company = get_company_by_ticker(db, ticker)
        facts = [
            FinancialFact(
                company_id=company.id,
                metric="revenue",
                value=100_000_000,
                unit="USD",
                end_date=date(2024, 12, 31),
                period_type="FY",
            ),
            FinancialFact(
                company_id=company.id,
                metric="net_income",
                value=20_000_000,
                unit="USD",
                end_date=date(2024, 12, 31),
                period_type="FY",
            ),
        ]
        for f in facts:
            db.add(f)
        db.commit()

    response = client.get(f"/companies/{ticker}/financials?format=minimal")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert "rev" in data[0]
    assert "ni" in data[0]
    assert data[0]["rev"] == 100_000_000
    assert "period" in data[0]
    assert "end" in data[0]


def test_get_financials_format_standard(client, engine):
    ticker = "MSFT"
    _create_company(client, ticker, "Microsoft")

    from sqlmodel import Session

    with Session(engine) as db:
        from repository import get_company_by_ticker

        company = get_company_by_ticker(db, ticker)
        fact = FinancialFact(
            company_id=company.id,
            metric="revenue",
            value=200_000_000,
            unit="USD",
            end_date=date(2024, 12, 31),
            period_type="FY",
        )
        db.add(fact)
        db.commit()

    response = client.get(f"/companies/{ticker}/financials?format=standard")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert "revenue" in data[0]
    assert data[0]["revenue"] == 200_000_000
    assert "period_type" in data[0]
    assert "end_date" in data[0]


def test_get_financials_fields_filter(client, engine):
    ticker = "GOOG"
    _create_company(client, ticker, "Google")

    from sqlmodel import Session

    with Session(engine) as db:
        from repository import get_company_by_ticker

        company = get_company_by_ticker(db, ticker)
        facts = [
            FinancialFact(
                company_id=company.id,
                metric="revenue",
                value=300_000_000,
                unit="USD",
                end_date=date(2024, 12, 31),
                period_type="FY",
            ),
            FinancialFact(
                company_id=company.id,
                metric="net_income",
                value=50_000_000,
                unit="USD",
                end_date=date(2024, 12, 31),
                period_type="FY",
            ),
            FinancialFact(
                company_id=company.id,
                metric="total_assets",
                value=400_000_000,
                unit="USD",
                end_date=date(2024, 12, 31),
                period_type="FY",
            ),
        ]
        for f in facts:
            db.add(f)
        db.commit()

    response = client.get(f"/companies/{ticker}/financials?fields=rev,ni")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert "rev" in data[0]
    assert "ni" in data[0]
    assert "ta" not in data[0]


def test_get_financials_fields_with_standard_format(client, engine):
    ticker = "NVDA"
    _create_company(client, ticker, "NVIDIA")

    from sqlmodel import Session

    with Session(engine) as db:
        from repository import get_company_by_ticker

        company = get_company_by_ticker(db, ticker)
        facts = [
            FinancialFact(
                company_id=company.id,
                metric="revenue",
                value=250_000_000,
                unit="USD",
                end_date=date(2024, 12, 31),
                period_type="FY",
            ),
            FinancialFact(
                company_id=company.id,
                metric="net_income",
                value=75_000_000,
                unit="USD",
                end_date=date(2024, 12, 31),
                period_type="FY",
            ),
        ]
        for f in facts:
            db.add(f)
        db.commit()

    response = client.get(f"/companies/{ticker}/financials?format=standard&fields=rev,ni")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert "revenue" in data[0]
    assert "net_income" in data[0]
    assert data[0]["revenue"] == 250_000_000


# --- Data Freshness _meta ---


def test_meta_minimal_format_keys(client, engine):
    ticker = "AAPL"
    _create_company(client, ticker, "Apple")

    from sqlmodel import Session

    with Session(engine) as db:
        from repository import get_company_by_ticker

        company = get_company_by_ticker(db, ticker)
        fact = FinancialFact(
            company_id=company.id,
            metric="revenue",
            value=100_000_000,
            unit="USD",
            end_date=date(2024, 12, 31),
            period_type="FY",
            filing_date=date(2025, 2, 15),
            source="edgar",
        )
        db.add(fact)
        db.commit()

    response = client.get(f"/companies/{ticker}/financials?format=minimal")
    assert response.status_code == 200
    data = response.json()
    meta = data[0]["_meta"]
    assert "fy" in meta
    assert "filed" in meta
    assert "src" in meta
    assert "age_days" in meta
    assert meta["fy"] == 2024
    assert meta["filed"] == "2025-02-15"
    assert meta["src"] == "edgar"
    assert isinstance(meta["age_days"], int)


def test_meta_standard_format_keys(client, engine):
    ticker = "MSFT"
    _create_company(client, ticker, "Microsoft")

    from sqlmodel import Session

    with Session(engine) as db:
        from repository import get_company_by_ticker

        company = get_company_by_ticker(db, ticker)
        fact = FinancialFact(
            company_id=company.id,
            metric="revenue",
            value=200_000_000,
            unit="USD",
            end_date=date(2024, 12, 31),
            period_type="FY",
            filing_date=date(2025, 2, 20),
            source="edgar",
        )
        db.add(fact)
        db.commit()

    response = client.get(f"/companies/{ticker}/financials?format=standard")
    assert response.status_code == 200
    data = response.json()
    meta = data[0]["_meta"]
    assert "fiscal_year" in meta
    assert "filed_date" in meta
    assert "source" in meta
    assert "data_age_days" in meta
    assert meta["fiscal_year"] == 2024
    assert meta["filed_date"] == "2025-02-20"


def test_meta_none_filing_date(client, engine):
    ticker = "GOOG"
    _create_company(client, ticker, "Google")

    from sqlmodel import Session

    with Session(engine) as db:
        from repository import get_company_by_ticker

        company = get_company_by_ticker(db, ticker)
        fact = FinancialFact(
            company_id=company.id,
            metric="revenue",
            value=150_000_000,
            unit="USD",
            end_date=date(2024, 12, 31),
            period_type="FY",
        )
        db.add(fact)
        db.commit()

    response = client.get(f"/companies/{ticker}/financials?format=minimal")
    assert response.status_code == 200
    data = response.json()
    meta = data[0]["_meta"]
    assert meta["age_days"] is None
    assert meta["filed"] is None


def test_meta_quarterly_includes_fq(client, engine):
    ticker = "META"
    _create_company(client, ticker, "Meta")

    from sqlmodel import Session

    with Session(engine) as db:
        from repository import get_company_by_ticker

        company = get_company_by_ticker(db, ticker)
        fact = FinancialFact(
            company_id=company.id,
            metric="revenue",
            value=50_000_000,
            unit="USD",
            end_date=date(2024, 3, 31),
            period_type="Q1",
            filing_date=date(2024, 5, 1),
            source="edgar",
        )
        db.add(fact)
        db.commit()

    response = client.get(f"/companies/{ticker}/financials?format=minimal")
    data = response.json()
    meta = data[0]["_meta"]
    assert "fq" in meta
    assert meta["fq"] == "Q1"
    assert "fy" not in meta

    response = client.get(f"/companies/{ticker}/financials?format=standard")
    data = response.json()
    meta = data[0]["_meta"]
    assert "fiscal_quarter" in meta
    assert meta["fiscal_quarter"] == "Q1"
    assert "fiscal_year" not in meta

