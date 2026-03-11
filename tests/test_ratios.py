import pytest
from datetime import date
from unittest.mock import patch
from sqlmodel import Session

from models import Company, DailyPrice, FinancialFact
from services.ratios import (
    RatioInputs,
    ComputedRatios,
    compute_ratios,
    build_ratio_inputs_from_facts,
    _safe_div,
)


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


# --- Unit tests ---


def test_safe_div_zero_denominator():
    assert _safe_div(100.0, 0) is None


def test_safe_div_none_numerator():
    assert _safe_div(None, 10.0) is None


def test_safe_div_none_denominator():
    assert _safe_div(10.0, None) is None


def test_safe_div_normal():
    assert _safe_div(10.0, 2.0) == pytest.approx(5.0)


def test_compute_ratios_all_present():
    inputs = RatioInputs(
        price=150.0,
        eps_diluted=5.0,
        eps_basic=4.8,
        shares_outstanding=1_000_000.0,
        stockholders_equity=500_000.0,
        total_liabilities=200_000.0,
        gross_profit=80_000.0,
        revenue=200_000.0,
        operating_income=40_000.0,
        net_income=30_000.0,
        depreciation_amortization=10_000.0,
    )
    r = compute_ratios(inputs)

    assert r.price_to_earnings == pytest.approx(30.0)  # 150 / 5
    assert r.price_to_book == pytest.approx(300.0)  # (150 * 1e6) / 5e5
    assert r.debt_to_equity == pytest.approx(0.4)  # 200k / 500k
    assert r.gross_margin == pytest.approx(0.4)  # 80k / 200k
    assert r.operating_margin == pytest.approx(0.2)  # 40k / 200k
    assert r.net_margin == pytest.approx(0.15)  # 30k / 200k
    assert r.return_on_equity == pytest.approx(0.06)  # 30k / 500k
    assert r.ebitda == pytest.approx(50_000.0)  # 40k + 10k
    assert r.free_cash_flow is None
    assert r.current_ratio is None


def test_compute_ratios_zero_equity_returns_null():
    inputs = RatioInputs(
        price=100.0,
        shares_outstanding=1_000_000.0,
        stockholders_equity=0.0,
        total_liabilities=200_000.0,
        net_income=10_000.0,
    )
    r = compute_ratios(inputs)
    assert r.price_to_book is None
    assert r.debt_to_equity is None
    assert r.return_on_equity is None


def test_compute_ratios_zero_revenue_returns_null_margins():
    inputs = RatioInputs(
        gross_profit=10_000.0,
        revenue=0.0,
        operating_income=5_000.0,
        net_income=3_000.0,
    )
    r = compute_ratios(inputs)
    assert r.gross_margin is None
    assert r.operating_margin is None
    assert r.net_margin is None


def test_compute_ratios_pe_fallback_to_eps_basic():
    inputs = RatioInputs(price=100.0, eps_diluted=None, eps_basic=4.0)
    r = compute_ratios(inputs)
    assert r.price_to_earnings == pytest.approx(25.0)


def test_compute_ratios_pe_null_when_no_eps():
    inputs = RatioInputs(price=100.0, eps_diluted=None, eps_basic=None)
    r = compute_ratios(inputs)
    assert r.price_to_earnings is None


def test_compute_ratios_ebitda_requires_both_inputs():
    inputs = RatioInputs(operating_income=50_000.0, depreciation_amortization=None)
    r = compute_ratios(inputs)
    assert r.ebitda is None

    inputs2 = RatioInputs(operating_income=None, depreciation_amortization=10_000.0)
    r2 = compute_ratios(inputs2)
    assert r2.ebitda is None


def test_compute_ratios_ebitda_computed_when_both_present():
    inputs = RatioInputs(operating_income=50_000.0, depreciation_amortization=10_000.0)
    r = compute_ratios(inputs)
    assert r.ebitda == pytest.approx(60_000.0)


def test_compute_ratios_fcf_always_null():
    inputs = RatioInputs(operating_cash_flow=100_000.0)
    r = compute_ratios(inputs)
    assert r.free_cash_flow is None


def test_compute_ratios_current_ratio_always_null():
    r = compute_ratios(RatioInputs())
    assert r.current_ratio is None


# --- Integration tests ---


def test_get_ratios_company_not_found(client):
    response = client.get("/companies/ZZZZ/ratios")
    assert response.status_code == 404
    data = response.json()
    assert data["error"] == "not_found"
    assert data["ticker"] == "ZZZZ"


def test_get_ratios_no_fy_facts(client, engine):
    company_id = _create_company(client, "NOFACT")
    # insert Q1 data only
    with Session(engine) as session:
        fact = FinancialFact(
            company_id=company_id,
            metric="revenue",
            value=10_000.0,
            unit="USD",
            end_date=date(2023, 12, 31),
            period_type="Q1",
            source="edgar",
        )
        session.add(fact)
        session.commit()

    response = client.get("/companies/NOFACT/ratios")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_get_ratios_success_minimal(client, engine):
    company_id = _create_company(client, "RTEST")
    _insert_facts(
        engine,
        company_id,
        {
            "revenue": 200_000.0,
            "gross_profit": 80_000.0,
            "operating_income": 40_000.0,
            "net_income": 30_000.0,
            "stockholders_equity": 500_000.0,
            "total_liabilities": 200_000.0,
            "eps_diluted": 5.0,
            "shares_outstanding": 1_000_000.0,
        },
    )
    _insert_price(engine, company_id, close=150.0, price_date=date(2024, 3, 1))

    response = client.get("/companies/RTEST/ratios")
    assert response.status_code == 200
    data = response.json()

    assert data["ticker"] == "RTEST"
    assert "pe" in data
    assert "gm" in data
    assert "_meta" in data
    assert "fy" in data["_meta"]
    assert "price_date" in data["_meta"]
    assert data["_meta"]["fy"] == 2023
    assert data["_meta"]["price_date"] == "2024-03-01"


def test_get_ratios_success_standard(client, engine):
    company_id = _create_company(client, "RSTD")
    _insert_facts(engine, company_id, {"revenue": 200_000.0, "net_income": 30_000.0})
    _insert_price(engine, company_id, close=100.0, price_date=date(2024, 1, 1))

    response = client.get("/companies/RSTD/ratios?format=standard")
    assert response.status_code == 200
    data = response.json()
    assert "net_margin" in data
    assert "price_to_earnings" in data
    assert "fiscal_year" in data["_meta"]
    assert "source" in data["_meta"]


def test_get_ratios_verbose_same_as_standard(client, engine):
    company_id = _create_company(client, "RVBOSE")
    _insert_facts(engine, company_id, {"revenue": 100_000.0, "net_income": 10_000.0})
    _insert_price(engine, company_id, close=50.0, price_date=date(2024, 1, 1))

    std = client.get("/companies/RVBOSE/ratios?format=standard").json()
    vbose = client.get("/companies/RVBOSE/ratios?format=verbose").json()
    assert std == vbose


def test_get_ratios_no_price_data(client, engine):
    company_id = _create_company(client, "NOPRICE")
    _insert_facts(
        engine,
        company_id,
        {
            "revenue": 200_000.0,
            "gross_profit": 80_000.0,
            "stockholders_equity": 500_000.0,
            "total_liabilities": 200_000.0,
        },
    )

    response = client.get("/companies/NOPRICE/ratios")
    assert response.status_code == 200
    data = response.json()
    assert data["pe"] is None
    assert data["pb"] is None
    assert data["gm"] == pytest.approx(0.4)
    assert data["de"] == pytest.approx(0.4)
    assert data["_meta"]["price_date"] is None


def test_get_ratios_fields_filter(client, engine):
    company_id = _create_company(client, "RFILT")
    _insert_facts(
        engine,
        company_id,
        {
            "revenue": 200_000.0,
            "gross_profit": 80_000.0,
            "net_income": 30_000.0,
            "eps_diluted": 5.0,
        },
    )
    _insert_price(engine, company_id, close=150.0, price_date=date(2024, 1, 1))

    response = client.get("/companies/RFILT/ratios?fields=pe,gm")
    assert response.status_code == 200
    data = response.json()

    assert "pe" in data
    assert "gm" in data
    assert "ticker" in data
    assert "_meta" in data
    # other ratio keys should not be present
    assert "de" not in data
    assert "nm" not in data


def test_get_ratios_fields_with_standard_format(client, engine):
    company_id = _create_company(client, "RFSTD")
    _insert_facts(engine, company_id, {"eps_diluted": 5.0})
    _insert_price(engine, company_id, close=150.0, price_date=date(2024, 1, 1))

    response = client.get("/companies/RFSTD/ratios?fields=pe&format=standard")
    assert response.status_code == 200
    data = response.json()
    assert "price_to_earnings" in data
    assert "pe" not in data


def test_get_ratios_meta_minimal_keys(client, engine):
    company_id = _create_company(client, "RMETA")
    _insert_facts(engine, company_id, {"revenue": 100_000.0})
    _insert_price(engine, company_id, close=50.0, price_date=date(2024, 1, 1))

    response = client.get("/companies/RMETA/ratios")
    assert response.status_code == 200
    meta = response.json()["_meta"]
    assert "fy" in meta
    assert "src" in meta
    assert "age_days" in meta
    assert "filed" in meta
    assert "price_date" in meta


def test_get_ratios_meta_standard_keys(client, engine):
    company_id = _create_company(client, "RMETAS")
    _insert_facts(engine, company_id, {"revenue": 100_000.0})
    _insert_price(engine, company_id, close=50.0, price_date=date(2024, 1, 1))

    response = client.get("/companies/RMETAS/ratios?format=standard")
    assert response.status_code == 200
    meta = response.json()["_meta"]
    assert "fiscal_year" in meta
    assert "source" in meta
    assert "data_age_days" in meta
    assert "filed_date" in meta
    assert "price_date" in meta


def test_get_ratios_invalid_format_returns_422(client, engine):
    company_id = _create_company(client, "RBADFORMAT")
    response = client.get("/companies/RBADFORMAT/ratios?format=bad")
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_params"
