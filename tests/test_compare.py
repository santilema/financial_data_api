import pytest
from datetime import date
from unittest.mock import patch
from sqlmodel import Session

from models import Company, DailyPrice, FinancialFact


# --- Helpers ---


def _create_company(client, ticker, name="Test Corp"):
    with patch("services.yfinance_client._fetch_data_sync") as mock_fetch:
        mock_fetch.return_value = (Company(ticker=ticker, name=name), [])
        response = client.post(f"/companies/{ticker}")
    assert response.status_code == 200
    return response.json()["id"]


def _insert_facts(
    engine,
    company_id,
    metrics: dict,
    end_date=date(2023, 9, 30),
    filing_date=date(2024, 1, 15),
):
    with Session(engine) as session:
        for metric, value in metrics.items():
            fact = FinancialFact(
                company_id=company_id,
                metric=metric,
                value=value,
                unit="USD",
                end_date=end_date,
                period_type="FY",
                filing_date=filing_date,
                source="edgar",
            )
            session.add(fact)
        session.commit()


def _insert_price(engine, company_id, close: float, price_date: date):
    with Session(engine) as session:
        dp = DailyPrice(
            company_id=company_id,
            date=price_date,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1_000_000,
        )
        session.add(dp)
        session.commit()


# --- Integration tests ---


def test_compare_missing_tickers_param(client):
    response = client.get("/compare")
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_params"


def test_compare_empty_tickers(client):
    response = client.get("/compare?tickers=,,,")
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_params"


def test_compare_too_many_tickers(client):
    tickers = ",".join([f"T{i:02d}" for i in range(11)])
    response = client.get(f"/compare?tickers={tickers}")
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_params"


def test_compare_ticker_not_found(client):
    response = client.get("/compare?tickers=ZZZZ")
    assert response.status_code == 200
    data = response.json()
    assert "ZZZZ" in data["data"]
    assert all(v is None for v in data["data"]["ZZZZ"].values())
    assert data["_meta"]["not_found"] == ["ZZZZ"]


def test_compare_not_found_key_absent_when_all_found(client, engine):
    company_id = _create_company(client, "CFOUND")
    _insert_facts(engine, company_id, {"revenue": 100_000.0})

    response = client.get("/compare?tickers=CFOUND")
    assert response.status_code == 200
    assert "not_found" not in response.json()["_meta"]


def test_compare_single_ticker_minimal(client, engine):
    company_id = _create_company(client, "CMIN")
    _insert_facts(engine, company_id, {
        "revenue": 200_000.0,
        "net_income": 30_000.0,
        "gross_profit": 80_000.0,
        "eps_diluted": 5.0,
        "shares_outstanding": 1_000_000.0,
        "stockholders_equity": 500_000.0,
        "total_liabilities": 200_000.0,
        "operating_income": 40_000.0,
    })
    _insert_price(engine, company_id, close=150.0, price_date=date(2024, 3, 1))

    response = client.get("/compare?tickers=CMIN")
    assert response.status_code == 200
    data = response.json()

    assert "metrics" in data
    assert "data" in data
    assert "CMIN" in data["data"]

    row = data["data"]["CMIN"]
    # minimal keys
    assert "rev" in row
    assert "ni" in row
    assert "pe" in row
    assert row["rev"] == pytest.approx(200_000.0)
    assert row["ni"] == pytest.approx(30_000.0)

    # metrics list uses minimal keys
    assert "rev" in data["metrics"]
    assert "pe" in data["metrics"]


def test_compare_single_ticker_standard(client, engine):
    company_id = _create_company(client, "CSTD")
    _insert_facts(engine, company_id, {
        "revenue": 200_000.0,
        "net_income": 30_000.0,
    })

    response = client.get("/compare?tickers=CSTD&format=standard")
    assert response.status_code == 200
    data = response.json()

    row = data["data"]["CSTD"]
    assert "revenue" in row
    assert "net_income" in row
    assert "revenue" in data["metrics"]


def test_compare_multi_ticker(client, engine):
    cid1 = _create_company(client, "CMUL1")
    cid2 = _create_company(client, "CMUL2")
    _insert_facts(engine, cid1, {"revenue": 100_000.0, "net_income": 10_000.0})
    _insert_facts(engine, cid2, {"revenue": 200_000.0, "net_income": 20_000.0})

    response = client.get("/compare?tickers=CMUL1,CMUL2&metrics=rev,ni")
    assert response.status_code == 200
    data = response.json()

    assert "CMUL1" in data["data"]
    assert "CMUL2" in data["data"]
    assert data["data"]["CMUL1"]["rev"] == pytest.approx(100_000.0)
    assert data["data"]["CMUL2"]["rev"] == pytest.approx(200_000.0)
    assert data["data"]["CMUL1"]["ni"] == pytest.approx(10_000.0)
    assert data["data"]["CMUL2"]["ni"] == pytest.approx(20_000.0)


def test_compare_metrics_filter(client, engine):
    company_id = _create_company(client, "CFILT")
    _insert_facts(engine, company_id, {
        "revenue": 200_000.0,
        "gross_profit": 80_000.0,
        "eps_diluted": 5.0,
    })
    _insert_price(engine, company_id, close=150.0, price_date=date(2024, 1, 1))

    response = client.get("/compare?tickers=CFILT&metrics=pe,gm")
    assert response.status_code == 200
    data = response.json()

    assert data["metrics"] == ["pe", "gm"]
    row = data["data"]["CFILT"]
    assert set(row.keys()) == {"pe", "gm"}


def test_compare_no_facts_all_null(client, engine):
    _create_company(client, "CNOFACT")
    # no facts inserted

    response = client.get("/compare?tickers=CNOFACT&metrics=rev,ni,pe")
    assert response.status_code == 200
    data = response.json()

    row = data["data"]["CNOFACT"]
    assert row["rev"] is None
    assert row["ni"] is None
    assert row["pe"] is None


def test_compare_meta_fy_used(client, engine):
    cid1 = _create_company(client, "CFY1")
    cid2 = _create_company(client, "CFY2")
    _insert_facts(engine, cid1, {"revenue": 100_000.0}, end_date=date(2023, 9, 30))
    _insert_facts(engine, cid2, {"revenue": 200_000.0}, end_date=date(2022, 12, 31))

    response = client.get("/compare?tickers=CFY1,CFY2&metrics=rev")
    assert response.status_code == 200
    meta = response.json()["_meta"]

    assert meta["fy_used"]["CFY1"] == 2023
    assert meta["fy_used"]["CFY2"] == 2022


def test_compare_verbose_same_as_standard(client, engine):
    company_id = _create_company(client, "CVBOSE")
    _insert_facts(engine, company_id, {"revenue": 100_000.0, "net_income": 10_000.0})
    _insert_price(engine, company_id, close=50.0, price_date=date(2024, 1, 1))

    std = client.get("/compare?tickers=CVBOSE&format=standard").json()
    vbose = client.get("/compare?tickers=CVBOSE&format=verbose").json()
    assert std == vbose


def test_compare_invalid_format_returns_422(client, engine):
    _create_company(client, "CFMT")
    response = client.get("/compare?tickers=CFMT&format=bad")
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_params"
