from typing import Literal
from datetime import date

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
    for fact in facts:
        if requested_metrics and fact.metric not in requested_metrics:
            continue

        key = (fact.end_date, fact.period_type)
        if key not in grouped:
            grouped[key] = {"end": str(fact.end_date), "period": fact.period_type}
            if ticker:
                grouped[key]["ticker"] = ticker

        _add_to_row(grouped[key], fact, format)

    return list(grouped.values())


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
