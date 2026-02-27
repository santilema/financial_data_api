import logging
from datetime import date

from models import FinancialFact

logger = logging.getLogger(__name__)

# Canonical metric -> XBRL tags in priority order (index 0 = highest priority)
TAXONOMY: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ],
    "cost_of_revenue": [
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
    ],
    "gross_profit": [
        "GrossProfit",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
    ],
    "total_assets": [
        "Assets",
    ],
    "total_liabilities": [
        "Liabilities",
    ],
    "stockholders_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "cash_and_equivalents": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsAndShortTermInvestments",
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
    ],
    "investing_cash_flow": [
        "NetCashProvidedByUsedInInvestingActivities",
    ],
    "financing_cash_flow": [
        "NetCashProvidedByUsedInFinancingActivities",
    ],
    "eps_basic": [
        "EarningsPerShareBasic",
    ],
    "eps_diluted": [
        "EarningsPerShareDiluted",
    ],
    "shares_outstanding": [
        "CommonStockSharesOutstanding",
        "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
    ],
    "dividends_per_share": [
        "CommonStockDividendsPerShareDeclared",
    ],
}

# Reverse lookup: xbrl_tag -> (canonical_metric, priority_index)
_TAG_TO_METRIC: dict[str, tuple[str, int]] = {}
for _metric, _tags in TAXONOMY.items():
    for _priority, _tag in enumerate(_tags):
        _TAG_TO_METRIC[_tag] = (_metric, _priority)

_VALID_FORMS = {"10-K", "10-Q"}
_FP_MAP = {
    "FY": "FY",
    "Q1": "Q1",
    "Q2": "Q2",
    "Q3": "Q3",
    "Q4": "Q4",
}


def normalize_company_facts(company_id: int, facts_json: dict) -> list[FinancialFact]:
    us_gaap = facts_json.get("facts", {}).get("us-gaap", {})
    if not us_gaap:
        return []

    # Key: (metric, end_date, period_type) -> (priority, FinancialFact)
    best: dict[tuple[str, str, str], tuple[int, FinancialFact]] = {}
    unmapped_tags: set[str] = set()

    for tag, concept_data in us_gaap.items():
        lookup = _TAG_TO_METRIC.get(tag)
        if lookup is None:
            unmapped_tags.add(tag)
            continue

        metric, priority = lookup
        units = concept_data.get("units", {})

        for unit_key, entries in units.items():
            for entry in entries:
                form = entry.get("form", "")
                if form not in _VALID_FORMS:
                    continue

                fp = entry.get("fp", "")
                period_type = _FP_MAP.get(fp)
                if period_type is None:
                    continue

                end_str = entry.get("end", "")
                if not end_str:
                    continue

                dedup_key = (metric, end_str, period_type)
                existing = best.get(dedup_key)
                if existing is not None and existing[0] <= priority:
                    continue

                filed_str = entry.get("filed", "")
                filing_date = None
                if filed_str:
                    try:
                        filing_date = date.fromisoformat(filed_str)
                    except ValueError:
                        pass

                fact = FinancialFact(
                    company_id=company_id,
                    metric=metric,
                    value=float(entry["val"]),
                    unit=unit_key,
                    end_date=date.fromisoformat(end_str),
                    period_type=period_type,
                    filing_date=filing_date,
                    source="SEC",
                )
                best[dedup_key] = (priority, fact)

    if unmapped_tags:
        logger.debug(
            "Unmapped XBRL tags (%d): %s",
            len(unmapped_tags),
            ", ".join(sorted(unmapped_tags)[:10]),
        )

    return [fact for _, fact in best.values()]


def get_tag_to_metric_map() -> dict[str, tuple[str, int]]:
    return dict(_TAG_TO_METRIC)


def get_taxonomy_seed_data() -> list[dict]:
    seed = []
    for metric, tags in TAXONOMY.items():
        for tag in tags:
            seed.append(
                {
                    "xbrl_tag": tag,
                    "metric": metric,
                    "description": f"{metric} mapped from {tag}",
                }
            )
    return seed
