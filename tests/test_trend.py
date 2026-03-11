import pytest
from datetime import date
from unittest.mock import patch
from sqlmodel import Session

from models import Company, FinancialFact


# --- Helpers ---


def _create_company(client, ticker, name="Test Corp"):
    with patch("services.yfinance_client._fetch_data_sync") as mock_fetch:
        mock_fetch.return_value = (Company(ticker=ticker, name=name), [])
        response = client.post(f"/companies/{ticker}")
    assert response.status_code == 200
    return response.json()["id"]


def _insert_fact(
    engine,
    company_id,
    metric,
    value,
    end_date,
    filing_date=date(2024, 2, 1),
):
    with Session(engine) as session:
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


def _insert_year(engine, company_id, metrics: dict, end_date: date):
    with Session(engine) as session:
        for metric, value in metrics.items():
            fact = FinancialFact(
                company_id=company_id,
                metric=metric,
                value=value,
                unit="USD",
                end_date=end_date,
                period_type="FY",
                filing_date=date(end_date.year + 1, 2, 1),
                source="edgar",
            )
            session.add(fact)
        session.commit()


# --- Tests ---


def test_trend_company_not_found(client):
    response = client.get("/companies/ZZZZ/trend")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_trend_no_financials(client, engine):
    _create_company(client, "TNOFIN")
    response = client.get("/companies/TNOFIN/trend")
    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == "TNOFIN"
    assert data["periods"] == []
    assert data["metrics"] == []
    assert data["_meta"] is None


def test_trend_basic_minimal(client, engine):
    cid = _create_company(client, "TMIN")
    _insert_year(
        engine, cid, {"revenue": 100_000.0, "net_income": 10_000.0}, date(2021, 12, 31)
    )
    _insert_year(
        engine, cid, {"revenue": 120_000.0, "net_income": 12_000.0}, date(2022, 12, 31)
    )
    _insert_year(
        engine, cid, {"revenue": 150_000.0, "net_income": 15_000.0}, date(2023, 12, 31)
    )

    response = client.get("/companies/TMIN/trend")
    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == "TMIN"
    periods = data["periods"]
    assert len(periods) == 3
    # minimal keys
    assert "rev" in periods[0]
    assert "ni" in periods[0]
    # years present
    assert [p["fy"] for p in periods] == [2021, 2022, 2023]


def test_trend_basic_standard(client, engine):
    cid = _create_company(client, "TSTD")
    _insert_year(
        engine, cid, {"revenue": 100_000.0, "net_income": 10_000.0}, date(2022, 12, 31)
    )

    response = client.get("/companies/TSTD/trend?format=standard")
    assert response.status_code == 200
    data = response.json()
    periods = data["periods"]
    assert len(periods) == 1
    assert "revenue" in periods[0]
    assert "net_income" in periods[0]
    assert "rev" not in periods[0]


def test_trend_metrics_filter(client, engine):
    cid = _create_company(client, "TFILT")
    _insert_year(
        engine,
        cid,
        {"revenue": 100_000.0, "net_income": 10_000.0, "gross_profit": 40_000.0},
        date(2022, 12, 31),
    )

    response = client.get("/companies/TFILT/trend?metrics=rev")
    assert response.status_code == 200
    data = response.json()
    periods = data["periods"]
    assert len(periods) == 1
    row = periods[0]
    assert "rev" in row
    assert "ni" not in row
    assert "gp" not in row


def test_trend_periods_limit(client, engine):
    cid = _create_company(client, "TPLIM")
    for yr in range(2018, 2024):
        _insert_year(engine, cid, {"revenue": float(yr * 1000)}, date(yr, 12, 31))

    response = client.get("/companies/TPLIM/trend?periods=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data["periods"]) == 2
    assert data["periods"][0]["fy"] == 2022
    assert data["periods"][1]["fy"] == 2023


def test_trend_periods_too_large(client):
    response = client.get("/companies/AAPL/trend?periods=21")
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_params"


def test_trend_computes_margins(client, engine):
    cid = _create_company(client, "TGROSS")
    _insert_year(
        engine,
        cid,
        {"revenue": 200_000.0, "gross_profit": 80_000.0},
        date(2023, 12, 31),
    )

    response = client.get("/companies/TGROSS/trend")
    assert response.status_code == 200
    data = response.json()
    periods = data["periods"]
    assert len(periods) == 1
    # gross margin = gross_profit / revenue = 0.4
    assert "gm" in periods[0]
    assert periods[0]["gm"] == pytest.approx(0.4)


def test_trend_chronological_order(client, engine):
    cid = _create_company(client, "TCHR")
    _insert_year(engine, cid, {"revenue": 300_000.0}, date(2023, 12, 31))
    _insert_year(engine, cid, {"revenue": 100_000.0}, date(2021, 12, 31))
    _insert_year(engine, cid, {"revenue": 200_000.0}, date(2022, 12, 31))

    response = client.get("/companies/TCHR/trend")
    assert response.status_code == 200
    periods = response.json()["periods"]
    assert [p["fy"] for p in periods] == [2021, 2022, 2023]


def test_trend_meta_coverage(client, engine):
    cid = _create_company(client, "TMETA")
    _insert_year(engine, cid, {"revenue": 100_000.0}, date(2019, 12, 31))
    _insert_year(engine, cid, {"revenue": 110_000.0}, date(2020, 12, 31))
    _insert_year(engine, cid, {"revenue": 120_000.0}, date(2021, 12, 31))

    response = client.get("/companies/TMETA/trend")
    assert response.status_code == 200
    meta = response.json()["_meta"]
    assert meta["coverage"]["from"] == 2019
    assert meta["coverage"]["to"] == 2021
    assert meta["periods_available"] == 3
