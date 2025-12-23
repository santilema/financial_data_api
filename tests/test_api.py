from unittest.mock import patch
from datetime import date, timedelta
from models import Instrument, DailyPrice
from services.yfinance_client import YahooFinanceError

# --- Create instruments ---


def test_add_instrument_success(client):
    ticker = "AAPL"
    mock_instrument = Instrument(ticker=ticker, name="Apple Inc.")

    mock_prices = [
        DailyPrice(
            instrument_id=None,  # db assigns this
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
        mock_fetch.return_value = (mock_instrument, mock_prices)
        response = client.post(f"/instruments/{ticker}")

    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == ticker
    assert data["id"] is not None

    # ensure price actually persisted
    price_response = client.get(f"/prices/{ticker}")
    assert len(price_response.json()) == 1


def test_add_duplicate_instrument(client):
    ticker = "MSFT"

    # first attempt
    with patch("services.yfinance_client._fetch_data_sync") as mock_fetch:
        mock_fetch.return_value = (Instrument(ticker=ticker, name="Micro"), [])
        client.post(f"/instruments/{ticker}")

    # second attempt should fail
    response = client.post(f"/instruments/{ticker}")

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


def test_add_instrument_yfinance_failure(client):
    ticker = "INVALID"

    with patch("services.yfinance_client._fetch_data_sync") as mock_fetch:
        mock_fetch.side_effect = YahooFinanceError("Ticker not found")
        response = client.post(f"/instruments/{ticker}")

    assert response.status_code == 400
    assert "Ticker not found" in response.json()["detail"]


# --- Retrieve Prices ---


def test_get_prices_not_found(client):
    response = client.get("/prices/INVALID")
    assert response.status_code == 404


# --- Sync Logic ---


def test_sync_prices_success(client):
    ticker = "GOOG"
    old_date = date.today() - timedelta(days=5)

    # 1. fill db with old data
    with patch("services.yfinance_client._fetch_data_sync") as mock_fetch_init:
        mock_fetch_init.return_value = (
            Instrument(ticker=ticker, name="Google"),
            [
                DailyPrice(
                    instrument_id=None,
                    date=old_date,
                    open=1,
                    high=1,
                    low=1,
                    close=1,
                    volume=100,
                )
            ],
        )
        client.post(f"/instruments/{ticker}")

    # 2. Sync new data (simulate 1 new day)
    new_price = DailyPrice(
        instrument_id=None,
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
            Instrument(ticker=ticker, name="Nvidia"),
            [
                DailyPrice(
                    instrument_id=None,
                    date=yesterday,
                    open=1,
                    high=1,
                    low=1,
                    close=1,
                    volume=1,
                )
            ],
        )
        client.post(f"/instruments/{ticker}")

    # sync should hit the guard clause and avoid external calls
    with patch("services.yfinance_client._fetch_data_since_sync") as mock_sync:
        response = client.post(f"/prices/{ticker}/sync")
        mock_sync.assert_not_called()

    assert response.status_code == 200
    assert "already up-to-date" in response.json()["message"]


def test_sync_prices_ticker_not_found(client):
    response = client.post("/prices/INVALID/sync")
    assert response.status_code == 404


# --- Delete Instruments ---


def test_delete_instrument_removes_prices(client):
    ticker = "TSLA"
    price_date = date(2024, 1, 1)

    with patch("services.yfinance_client._fetch_data_sync") as mock_fetch:
        mock_fetch.return_value = (
            Instrument(ticker=ticker, name="Tesla"),
            [
                DailyPrice(
                    instrument_id=None,
                    date=price_date,
                    open=1,
                    high=1,
                    low=1,
                    close=1,
                    volume=10,
                )
            ],
        )
        client.post(f"/instruments/{ticker}")

    response = client.delete(f"/instruments/{ticker}")
    assert response.status_code == 200
    data = response.json()
    assert data["prices_deleted"] == 1
    assert data["instrument_deleted"] == 1
    assert ticker in data["message"]

    # instrument and prices should be gone
    price_response = client.get(f"/prices/{ticker}")
    assert price_response.status_code == 404


def test_delete_instrument_not_found_does_not_touch_other_data(client):
    existing_ticker = "IBM"
    with patch("services.yfinance_client._fetch_data_sync") as mock_fetch:
        mock_fetch.return_value = (
            Instrument(ticker=existing_ticker, name="IBM"),
            [
                DailyPrice(
                    instrument_id=None,
                    date=date(2023, 1, 1),
                    open=1,
                    high=1,
                    low=1,
                    close=1,
                    volume=100,
                )
            ],
        )
        client.post(f"/instruments/{existing_ticker}")

    response = client.delete("/instruments/UNKNOWN")
    assert response.status_code == 404

    # existing ticker data should remain untouched
    existing_prices = client.get(f"/prices/{existing_ticker}")
    assert existing_prices.status_code == 200
    assert len(existing_prices.json()) == 1
