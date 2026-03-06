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


def test_get_profile_company_not_found(client):
    response = client.get("/companies/ZZZZ")
    assert response.status_code == 404
    data = response.json()
    assert data["error"] == "not_found"
    assert data["ticker"] == "ZZZZ"


def test_get_profile_no_facts_no_price(client, engine):
    _create_company(client, "PNONE", name="No Data Corp")
    response = client.get("/companies/PNONE")
    assert response.status_code == 200
    data = response.json()

    assert data["ticker"] == "PNONE"
    assert data["name"] == "No Data Corp"
    assert "rev" not in data
    assert "ni" not in data
    assert "eps" not in data
    assert "gm" not in data
    assert "pe" not in data
    assert "mkt_cap" not in data
    assert "price" not in data
    assert data["_meta"]["fy"] is None


def test_get_profile_with_facts_no_price(client, engine):
    company_id = _create_company(client, "PFACTS")
    _insert_facts(
        engine,
        company_id,
        {
            "revenue": 100_000.0,
            "net_income": 20_000.0,
            "gross_profit": 50_000.0,
        },
    )

    response = client.get("/companies/PFACTS")
    assert response.status_code == 200
    data = response.json()

    assert "rev" in data
    assert "ni" in data
    assert "gm" in data
    assert "price" not in data
    assert "mkt_cap" not in data
    assert "pe" not in data
    assert data["_meta"]["src"] == "edgar"


def test_get_profile_with_price_no_facts(client, engine):
    company_id = _create_company(client, "PPRICE")
    _insert_price(engine, company_id, close=150.0, price_date=date(2024, 3, 1))

    response = client.get("/companies/PPRICE")
    assert response.status_code == 200
    data = response.json()

    assert data["price"] == pytest.approx(150.0)
    assert "rev" not in data
    assert "ni" not in data
    assert "eps" not in data
    assert "gm" not in data
    assert "pe" not in data
    assert data["_meta"]["src"] == "yfinance"


def test_get_profile_full_minimal(client, engine):
    company_id = _create_company(client, "PFULL")
    _insert_facts(
        engine,
        company_id,
        {
            "revenue": 200_000.0,
            "net_income": 30_000.0,
            "gross_profit": 80_000.0,
            "eps_diluted": 5.0,
            "shares_outstanding": 1_000_000.0,
        },
    )
    _insert_price(engine, company_id, close=150.0, price_date=date(2024, 3, 1))

    response = client.get("/companies/PFULL")
    assert response.status_code == 200
    data = response.json()

    assert data["ticker"] == "PFULL"
    assert data["price"] == pytest.approx(150.0)
    assert data["mkt_cap"] == pytest.approx(150.0 * 1_000_000.0)
    assert data["rev"] == pytest.approx(200_000.0)
    assert data["ni"] == pytest.approx(30_000.0)
    assert data["eps"] == pytest.approx(5.0)
    assert data["gm"] == pytest.approx(80_000.0 / 200_000.0)
    assert data["pe"] == pytest.approx(150.0 / 5.0)
    assert "_meta" in data


def test_get_profile_full_standard(client, engine):
    company_id = _create_company(client, "PFSTD")
    _insert_facts(
        engine,
        company_id,
        {
            "revenue": 200_000.0,
            "net_income": 30_000.0,
            "gross_profit": 80_000.0,
            "eps_diluted": 5.0,
            "shares_outstanding": 1_000_000.0,
        },
    )
    _insert_price(engine, company_id, close=150.0, price_date=date(2024, 3, 1))

    response = client.get("/companies/PFSTD?format=standard")
    assert response.status_code == 200
    data = response.json()

    assert "market_cap" in data
    assert "revenue" in data
    assert "net_income" in data
    assert "eps_diluted" in data
    assert "gross_margin" in data
    assert "price_to_earnings" in data
    assert "mkt_cap" not in data
    assert "rev" not in data


def test_get_profile_verbose_same_as_standard(client, engine):
    company_id = _create_company(client, "PFVBOSE")
    _insert_facts(
        engine,
        company_id,
        {
            "revenue": 100_000.0,
            "net_income": 10_000.0,
            "gross_profit": 40_000.0,
            "eps_diluted": 3.0,
        },
    )
    _insert_price(engine, company_id, close=90.0, price_date=date(2024, 1, 1))

    std = client.get("/companies/PFVBOSE?format=standard").json()
    vbose = client.get("/companies/PFVBOSE?format=verbose").json()
    assert std == vbose


def test_get_profile_meta_minimal_keys(client, engine):
    company_id = _create_company(client, "PMETA")
    _insert_facts(engine, company_id, {"revenue": 100_000.0})
    _insert_price(engine, company_id, close=50.0, price_date=date(2024, 1, 1))

    response = client.get("/companies/PMETA")
    assert response.status_code == 200
    meta = response.json()["_meta"]
    assert "fy" in meta
    assert "src" in meta
    assert "age_days" in meta
    assert "filed" in meta
    assert "price_date" in meta


def test_get_profile_meta_standard_keys(client, engine):
    company_id = _create_company(client, "PMETAS")
    _insert_facts(engine, company_id, {"revenue": 100_000.0})
    _insert_price(engine, company_id, close=50.0, price_date=date(2024, 1, 1))

    response = client.get("/companies/PMETAS?format=standard")
    assert response.status_code == 200
    meta = response.json()["_meta"]
    assert "fiscal_year" in meta
    assert "source" in meta
    assert "data_age_days" in meta
    assert "filed_date" in meta
    assert "price_date" in meta


def test_get_profile_meta_source_edgar_plus_yfinance(client, engine):
    company_id = _create_company(client, "PBOTH")
    _insert_facts(engine, company_id, {"revenue": 100_000.0})
    _insert_price(engine, company_id, close=50.0, price_date=date(2024, 1, 1))

    response = client.get("/companies/PBOTH")
    assert response.status_code == 200
    assert response.json()["_meta"]["src"] == "edgar+yfinance"


def test_get_profile_invalid_format_returns_422(client, engine):
    _create_company(client, "PFBAD")
    response = client.get("/companies/PFBAD?format=bad")
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_params"
