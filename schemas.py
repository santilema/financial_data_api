from typing import Literal, Optional
from datetime import date, timedelta
from fastapi import HTTPException
from pydantic import BaseModel
from services.ratios import build_ratio_inputs_from_facts, compute_ratios

MINIMAL_TO_STANDARD: dict[str, str] = {
    "rev": "revenue",
    "ni": "net_income",
    "gp": "gross_profit",
    "oi": "operating_income",
    "eps": "eps_basic",
    "epsd": "eps_diluted",
    "ta": "total_assets",
    "tl": "total_liabilities",
    "eq": "stockholders_equity",
    "cash": "cash_and_equivalents",
    "ocf": "operating_cash_flow",
    "icf": "investing_cash_flow",
    "fcf_cf": "financing_cash_flow",
    "cor": "cost_of_revenue",
    "so": "shares_outstanding",
    "dps": "dividends_per_share",
    "ebitda": "ebitda",
    "de": "debt_to_equity",
    "gm": "gross_margin",
    "om": "operating_margin",
    "pe": "price_to_earnings",
    "nm": "net_margin",
    "roe": "return_on_equity",
    "pb": "price_to_book",
    "cr": "current_ratio",
    "fcf": "free_cash_flow",
}

STANDARD_TO_MINIMAL: dict[str, str] = {v: k for k, v in MINIMAL_TO_STANDARD.items()}

METRIC_DESCRIPTIONS: dict[str, str] = {
    "revenue": "Total revenue",
    "net_income": "Net income",
    "gross_profit": "Gross profit",
    "operating_income": "Operating income",
    "eps_basic": "Basic earnings per share",
    "eps_diluted": "Diluted earnings per share",
    "total_assets": "Total assets",
    "total_liabilities": "Total liabilities",
    "stockholders_equity": "Stockholders equity",
    "cash_and_equivalents": "Cash and cash equivalents",
    "operating_cash_flow": "Operating cash flow",
    "investing_cash_flow": "Investing cash flow",
    "financing_cash_flow": "Financing cash flow",
    "cost_of_revenue": "Cost of revenue",
    "shares_outstanding": "Shares outstanding",
    "dividends_per_share": "Dividends per share",
    "ebitda": "Earnings before interest, taxes, depreciation, and amortization",
    "debt_to_equity": "Debt to equity ratio",
    "gross_margin": "Gross margin",
    "operating_margin": "Operating margin",
    "price_to_earnings": "Price to earnings ratio",
    "net_margin": "Net margin",
    "return_on_equity": "Return on equity",
    "price_to_book": "Price to book ratio",
    "current_ratio": "Current ratio",
    "free_cash_flow": "Free cash flow",
    "depreciation_amortization": "Depreciation and amortization",
}

METRIC_UNITS: dict[str, str] = {
    "revenue": "USD",
    "net_income": "USD",
    "gross_profit": "USD",
    "operating_income": "USD",
    "total_assets": "USD",
    "total_liabilities": "USD",
    "stockholders_equity": "USD",
    "cash_and_equivalents": "USD",
    "operating_cash_flow": "USD",
    "investing_cash_flow": "USD",
    "financing_cash_flow": "USD",
    "cost_of_revenue": "USD",
    "shares_outstanding": "shares",
    "dividends_per_share": "USD/shares",
    "eps_basic": "USD/shares",
    "eps_diluted": "USD/shares",
    "ebitda": "USD",
    "free_cash_flow": "USD",
    "depreciation_amortization": "USD",
}


def _resolve_fields(fields: list[str] | None) -> list[str] | None:
    """Convert field names (always minimal keys) to DB metric names."""
    if not fields:
        return None
    return [MINIMAL_TO_STANDARD.get(f, f) for f in fields]


def transform_financials(
    facts: list,
    format: Literal["minimal", "standard", "verbose"] = "minimal",
    fields: list[str] | None = None,
    ticker: str | None = None,
) -> list[dict]:
    if not facts:
        return []

    requested_metrics = _resolve_fields(fields)

    results = []
    for fact in facts:
        if requested_metrics and fact.metric not in requested_metrics:
            continue

        row = _build_row(fact, format)
        if ticker:
            row["ticker"] = ticker
        results.append(row)

    return results


def _build_row(fact, format: str) -> dict:
    metric = fact.metric

    if format == "minimal":
        key = STANDARD_TO_MINIMAL.get(metric, metric)
        row = {key: fact.value}
    elif format == "standard":
        row = {metric: fact.value}
    else:
        row = {
            "metric": metric,
            "value": fact.value,
            "unit": fact.unit,
            "end_date": str(fact.end_date),
            "period_type": fact.period_type,
            "description": METRIC_DESCRIPTIONS.get(metric, ""),
        }

    return row


def _build_meta(
    facts: list,
    format: Literal["minimal", "standard", "verbose"],
    end_date: date,
    period_type: str,
) -> dict:
    filing_dates = [f.filing_date for f in facts if f.filing_date is not None]
    latest_filing = max(filing_dates) if filing_dates else None

    sources = [f.source for f in facts if f.source is not None]
    latest_source = sources[-1] if sources else "edgar"

    fy = end_date.year if period_type.startswith("FY") else None
    fq = period_type if period_type.startswith("Q") else None

    age = (date.today() - latest_filing).days if latest_filing is not None else None

    if format == "minimal":
        meta: dict = {"src": latest_source, "age_days": age}
        if fy is not None:
            meta["fy"] = fy
        if fq is not None:
            meta["fq"] = fq
        meta["filed"] = str(latest_filing) if latest_filing else None
    else:
        meta = {"source": latest_source, "data_age_days": age}
        if fy is not None:
            meta["fiscal_year"] = fy
        if fq is not None:
            meta["fiscal_quarter"] = fq
        meta["filed_date"] = str(latest_filing) if latest_filing else None

    return meta


def transform_financials_by_period(
    facts: list,
    format: Literal["minimal", "standard", "verbose"] = "minimal",
    fields: list[str] | None = None,
    ticker: str | None = None,
) -> list[dict]:
    if not facts:
        return []

    requested_metrics = _resolve_fields(fields)

    grouped: dict[tuple, dict] = {}
    group_facts: dict[tuple, list] = {}
    for fact in facts:
        if requested_metrics and fact.metric not in requested_metrics:
            continue

        key = (fact.end_date, fact.period_type)
        if key not in grouped:
            grouped[key] = {"end": str(fact.end_date), "period": fact.period_type}
            group_facts[key] = []
            if ticker:
                grouped[key]["ticker"] = ticker

        group_facts[key].append(fact)
        _add_to_row(grouped[key], fact, format)

    for key, row in grouped.items():
        end_date, period_type = key
        row["_meta"] = _build_meta(group_facts[key], format, end_date, period_type)

    return list(grouped.values())


class ApiError(BaseModel):
    error: str
    message: str
    ticker: str | None = None
    field: str | None = None
    detail: str | None = None


class ApiException(HTTPException):
    def __init__(self, status_code: int, error: str, message: str, **ctx):
        super().__init__(status_code=status_code, detail=message)
        self.error = error
        self.ctx = ctx


RATIO_STANDARD_KEYS = [
    "price_to_earnings",
    "price_to_book",
    "debt_to_equity",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "return_on_equity",
    "ebitda",
    "free_cash_flow",
    "current_ratio",
]

COMPARISON_FACT_METRICS: list[str] = [
    "revenue",
    "net_income",
    "gross_profit",
    "operating_income",
    "eps_basic",
    "eps_diluted",
    "total_assets",
    "total_liabilities",
    "stockholders_equity",
    "cash_and_equivalents",
    "operating_cash_flow",
    "investing_cash_flow",
    "financing_cash_flow",
    "cost_of_revenue",
    "shares_outstanding",
    "dividends_per_share",
]


def _build_ratios_meta(inputs, format: str) -> dict:
    age = (date.today() - inputs.filing_date).days if inputs.filing_date else None
    fy = inputs.fy_end_date.year if inputs.fy_end_date else None
    price_date = str(inputs.price_date) if inputs.price_date else None
    filed = str(inputs.filing_date) if inputs.filing_date else None
    source = inputs.source or "edgar"

    if format == "minimal":
        return {
            "src": source,
            "age_days": age,
            "filed": filed,
            "fy": fy,
            "price_date": price_date,
        }
    else:
        return {
            "source": source,
            "data_age_days": age,
            "filed_date": filed,
            "fiscal_year": fy,
            "price_date": price_date,
        }


def transform_ratios(
    ratios,
    inputs,
    ticker: str,
    format: Literal["minimal", "standard", "verbose"] = "minimal",
    fields: Optional[list[str]] = None,
) -> dict:
    raw = {k: getattr(ratios, k) for k in RATIO_STANDARD_KEYS}

    requested_metrics = _resolve_fields(fields) if fields else None

    result: dict = {"ticker": ticker}

    for standard_key, value in raw.items():
        if requested_metrics and standard_key not in requested_metrics:
            continue
        if format == "minimal":
            out_key = STANDARD_TO_MINIMAL.get(standard_key, standard_key)
        else:
            out_key = standard_key
        result[out_key] = value

    result["_meta"] = _build_ratios_meta(inputs, format)
    return result


def _build_profile_meta(inputs, price_row, format: str) -> dict:
    has_facts = inputs is not None
    has_price = price_row is not None

    if has_facts and has_price:
        source = "edgar+yfinance"
    elif has_facts:
        source = "edgar"
    elif has_price:
        source = "yfinance"
    else:
        source = "edgar"

    fy = inputs.fy_end_date.year if has_facts and inputs.fy_end_date else None
    filed = str(inputs.filing_date) if has_facts and inputs.filing_date else None
    age = (
        (date.today() - inputs.filing_date).days
        if has_facts and inputs.filing_date
        else None
    )
    price_date = str(price_row.date) if has_price else None

    if format == "minimal":
        return {
            "src": source,
            "age_days": age,
            "filed": filed,
            "fy": fy,
            "price_date": price_date,
        }
    else:
        return {
            "source": source,
            "data_age_days": age,
            "filed_date": filed,
            "fiscal_year": fy,
            "price_date": price_date,
        }


def transform_company_profile(
    company,
    ratios,
    inputs,
    price_row,
    ticker: str,
    format: Literal["minimal", "standard", "verbose"] = "minimal",
) -> dict:
    has_facts = inputs is not None
    has_price = price_row is not None

    result: dict = {
        "ticker": ticker,
        "name": company.name,
        "sector": company.sector,
        "industry": company.industry,
        "exchange": company.exchange,
        "cik": company.cik,
    }

    if has_price:
        mkt_cap = (
            price_row.close * inputs.shares_outstanding
            if has_facts and inputs.shares_outstanding is not None
            else None
        )
        result["price"] = price_row.close
        if format == "minimal":
            result["mkt_cap"] = mkt_cap
        else:
            result["market_cap"] = mkt_cap

    if has_facts:
        eps_val = (
            inputs.eps_diluted if inputs.eps_diluted is not None else inputs.eps_basic
        )
        gm_val = ratios.gross_margin if ratios is not None else None
        if format == "minimal":
            result["rev"] = inputs.revenue
            result["ni"] = inputs.net_income
            result["eps"] = eps_val
            result["gm"] = gm_val
        else:
            result["revenue"] = inputs.revenue
            result["net_income"] = inputs.net_income
            result["eps_diluted"] = eps_val
            result["gross_margin"] = gm_val

    if has_facts and has_price:
        pe_val = ratios.price_to_earnings if ratios is not None else None
        if format == "minimal":
            result["pe"] = pe_val
        else:
            result["price_to_earnings"] = pe_val

    result["_meta"] = _build_profile_meta(inputs, price_row, format)
    return result


def _build_ticker_flat(facts: list, ratios) -> dict:
    fact_map = {f.metric: f.value for f in facts}
    result: dict = {}
    for key in COMPARISON_FACT_METRICS:
        result[key] = fact_map.get(key)
    for key in RATIO_STANDARD_KEYS:
        result[key] = getattr(ratios, key, None) if ratios is not None else None
    return result


def transform_comparison(
    ticker_results: list[dict],
    requested_metrics: list[str] | None,
    format: str,
) -> dict:
    if requested_metrics:
        active_std = [MINIMAL_TO_STANDARD.get(m, m) for m in requested_metrics]
    else:
        active_std = COMPARISON_FACT_METRICS + RATIO_STANDARD_KEYS

    data: dict = {}
    fy_used: dict = {}
    not_found: list[str] = []

    for item in ticker_results:
        ticker = item["ticker"]
        if not item["found"]:
            row = {key: None for key in active_std}
            not_found.append(ticker)
            fy_used[ticker] = None
        else:
            flat = _build_ticker_flat(item["facts"], item["ratios"])
            row = {key: flat.get(key) for key in active_std}
            inputs = item["inputs"]
            fy_used[ticker] = (
                inputs.fy_end_date.year if inputs and inputs.fy_end_date else None
            )

        if format == "minimal":
            row = {STANDARD_TO_MINIMAL.get(k, k): v for k, v in row.items()}
        data[ticker] = row

    if format == "minimal":
        metrics = [STANDARD_TO_MINIMAL.get(k, k) for k in active_std]
    else:
        metrics = list(active_std)

    meta: dict = {"fy_used": fy_used}
    if not_found:
        meta["not_found"] = not_found

    return {"metrics": metrics, "data": data, "_meta": meta}


def transform_trend(
    facts: list,
    ticker: str,
    format: Literal["minimal", "standard", "verbose"],
    metrics_filter: list[str] | None,
    periods: int,
) -> dict:
    if not facts:
        return {"ticker": ticker, "metrics": [], "periods": [], "_meta": None}

    # Group FY facts by year
    by_year: dict[int, list] = {}
    for fact in facts:
        if fact.end_date is None:
            continue
        yr = fact.end_date.year
        by_year.setdefault(yr, []).append(fact)

    if not by_year:
        return {"ticker": ticker, "metrics": [], "periods": [], "_meta": None}

    # Resolve metrics_filter to standard names for lookup
    requested_std: set[str] | None = None
    if metrics_filter:
        requested_std = {MINIMAL_TO_STANDARD.get(m, m) for m in metrics_filter}

    sorted_years = sorted(by_year.keys())
    sorted_years = sorted_years[-periods:]

    seen_keys: list[str] = []
    seen_keys_set: set[str] = set()
    period_rows = []

    for yr in sorted_years:
        yr_facts = by_year[yr]
        inputs = build_ratio_inputs_from_facts(yr_facts, price=None, price_date=None)
        ratios = compute_ratios(inputs)

        row: dict = {"fy": yr}

        # Raw metrics from facts
        for fact in yr_facts:
            std_key = fact.metric
            if requested_std is not None and std_key not in requested_std:
                continue
            out_key = STANDARD_TO_MINIMAL.get(std_key, std_key) if format == "minimal" else std_key
            row[out_key] = fact.value
            if out_key not in seen_keys_set:
                seen_keys.append(out_key)
                seen_keys_set.add(out_key)

        # Computed ratios
        for std_key in RATIO_STANDARD_KEYS:
            if requested_std is not None and std_key not in requested_std:
                continue
            val = getattr(ratios, std_key, None)
            if val is None:
                continue
            out_key = STANDARD_TO_MINIMAL.get(std_key, std_key) if format == "minimal" else std_key
            row[out_key] = val
            if out_key not in seen_keys_set:
                seen_keys.append(out_key)
                seen_keys_set.add(out_key)

        period_rows.append(row)

    meta = {
        "coverage": {"from": sorted_years[0], "to": sorted_years[-1]},
        "periods_available": len(sorted_years),
    }

    return {
        "ticker": ticker,
        "metrics": seen_keys,
        "periods": period_rows,
        "_meta": meta,
    }


def _add_to_row(row: dict, fact, format: str) -> None:
    metric = fact.metric

    if format == "minimal":
        key = STANDARD_TO_MINIMAL.get(metric, metric)
        row[key] = fact.value
    elif format == "standard":
        row[metric] = fact.value
        row["period_type"] = fact.period_type
        row["end_date"] = str(fact.end_date)
    else:
        row.setdefault("period_type", fact.period_type)
        row.setdefault("end_date", str(fact.end_date))
        row.setdefault("metrics", [])
        row["metrics"].append(
            {
                "metric": metric,
                "value": fact.value,
                "unit": fact.unit,
                "description": METRIC_DESCRIPTIONS.get(metric, ""),
            }
        )
