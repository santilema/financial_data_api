import json
from pathlib import Path

from services.taxonomy import (
    TAXONOMY,
    normalize_company_facts,
    get_tag_to_metric_map,
    get_taxonomy_seed_data,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "aapl_companyfacts.json"


def _load_fixture() -> dict:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


def test_basic_extraction():
    facts = normalize_company_facts(1, _load_fixture())
    metrics = {f.metric for f in facts}
    assert "revenue" in metrics
    assert "net_income" in metrics
    assert "eps_basic" in metrics
    assert "total_assets" in metrics


def test_priority_resolution():
    """Higher-priority tag (RevenueFromContract...) wins over Revenues for same period."""
    facts = normalize_company_facts(1, _load_fixture())
    fy_revenue = [
        f for f in facts
        if f.metric == "revenue" and f.period_type == "FY" and str(f.end_date) == "2023-09-30"
    ]
    assert len(fy_revenue) == 1
    assert fy_revenue[0].value == 383285000000


def test_8k_filtered_out():
    """8-K form entries should not appear in results."""
    facts = normalize_company_facts(1, _load_fixture())
    # Assets has both 10-K and 8-K for same date, only 10-K should survive
    assets = [f for f in facts if f.metric == "total_assets"]
    assert len(assets) == 1
    assert assets[0].value == 352583000000


def test_non_usd_units():
    """EarningsPerShareBasic uses USD/shares unit."""
    facts = normalize_company_facts(1, _load_fixture())
    eps = [f for f in facts if f.metric == "eps_basic"]
    assert len(eps) == 1
    assert eps[0].unit == "USD/shares"
    assert eps[0].value == 6.16


def test_empty_facts():
    result = normalize_company_facts(1, {})
    assert result == []


def test_missing_us_gaap():
    result = normalize_company_facts(1, {"facts": {}})
    assert result == []


def test_reverse_lookup_completeness():
    tag_map = get_tag_to_metric_map()
    for metric, tags in TAXONOMY.items():
        for tag in tags:
            assert tag in tag_map
            assert tag_map[tag][0] == metric


def test_seed_data_format():
    seed = get_taxonomy_seed_data()
    assert len(seed) > 0
    for entry in seed:
        assert "xbrl_tag" in entry
        assert "metric" in entry
        assert "description" in entry


def test_q1_period_type():
    facts = normalize_company_facts(1, _load_fixture())
    q1_revenue = [f for f in facts if f.metric == "revenue" and f.period_type == "Q1"]
    assert len(q1_revenue) == 1
    assert q1_revenue[0].value == 119575000000


def test_unmapped_tag_not_in_results():
    """SomeObscureTag should not produce any FinancialFact."""
    facts = normalize_company_facts(1, _load_fixture())
    metrics = {f.metric for f in facts}
    # SomeObscureTag is not in taxonomy, should not appear
    for f in facts:
        assert f.source == "SEC"
    # Just verify we don't have unexpected metrics
    expected = {"revenue", "net_income", "eps_basic", "total_assets"}
    assert metrics == expected
