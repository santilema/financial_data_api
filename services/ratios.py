from dataclasses import dataclass
from datetime import date
from typing import Optional


def _safe_div(numerator, denominator) -> Optional[float]:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


@dataclass
class RatioInputs:
    price: Optional[float] = None
    price_date: Optional[date] = None
    eps_diluted: Optional[float] = None
    eps_basic: Optional[float] = None
    shares_outstanding: Optional[float] = None
    stockholders_equity: Optional[float] = None
    total_liabilities: Optional[float] = None
    gross_profit: Optional[float] = None
    revenue: Optional[float] = None
    operating_income: Optional[float] = None
    net_income: Optional[float] = None
    operating_cash_flow: Optional[float] = None
    depreciation_amortization: Optional[float] = None
    fy_end_date: Optional[date] = None
    filing_date: Optional[date] = None
    source: Optional[str] = None


@dataclass
class ComputedRatios:
    price_to_earnings: Optional[float] = None
    price_to_book: Optional[float] = None
    debt_to_equity: Optional[float] = None
    gross_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    net_margin: Optional[float] = None
    return_on_equity: Optional[float] = None
    ebitda: Optional[float] = None
    free_cash_flow: Optional[float] = None
    current_ratio: Optional[float] = None


def compute_ratios(inputs: RatioInputs) -> ComputedRatios:
    eps = inputs.eps_diluted if inputs.eps_diluted is not None else inputs.eps_basic

    pe = _safe_div(inputs.price, eps)

    market_cap = (
        inputs.price * inputs.shares_outstanding
        if inputs.price is not None and inputs.shares_outstanding is not None
        else None
    )
    pb = _safe_div(market_cap, inputs.stockholders_equity)

    de = _safe_div(inputs.total_liabilities, inputs.stockholders_equity)
    gm = _safe_div(inputs.gross_profit, inputs.revenue)
    om = _safe_div(inputs.operating_income, inputs.revenue)
    nm = _safe_div(inputs.net_income, inputs.revenue)
    roe = _safe_div(inputs.net_income, inputs.stockholders_equity)

    if (
        inputs.operating_income is not None
        and inputs.depreciation_amortization is not None
    ):
        ebitda = inputs.operating_income + inputs.depreciation_amortization
    else:
        ebitda = None

    # capex not in taxonomy
    fcf = None

    # current_assets/liabilities not tracked
    cr = None

    return ComputedRatios(
        price_to_earnings=pe,
        price_to_book=pb,
        debt_to_equity=de,
        gross_margin=gm,
        operating_margin=om,
        net_margin=nm,
        return_on_equity=roe,
        ebitda=ebitda,
        free_cash_flow=fcf,
        current_ratio=cr,
    )


def build_ratio_inputs_from_facts(facts, price, price_date) -> RatioInputs:
    fact_map = {f.metric: f.value for f in facts}

    filing_dates = [f.filing_date for f in facts if f.filing_date is not None]
    filing_date = max(filing_dates) if filing_dates else None

    source = facts[-1].source if facts and facts[-1].source else "edgar"

    end_dates = [f.end_date for f in facts if f.end_date is not None]
    fy_end_date = max(end_dates) if end_dates else None

    return RatioInputs(
        price=price,
        price_date=price_date,
        eps_diluted=fact_map.get("eps_diluted"),
        eps_basic=fact_map.get("eps_basic"),
        shares_outstanding=fact_map.get("shares_outstanding"),
        stockholders_equity=fact_map.get("stockholders_equity"),
        total_liabilities=fact_map.get("total_liabilities"),
        gross_profit=fact_map.get("gross_profit"),
        revenue=fact_map.get("revenue"),
        operating_income=fact_map.get("operating_income"),
        net_income=fact_map.get("net_income"),
        operating_cash_flow=fact_map.get("operating_cash_flow"),
        depreciation_amortization=fact_map.get("depreciation_amortization"),
        fy_end_date=fy_end_date,
        filing_date=filing_date,
        source=source,
    )
