import pytest
from datetime import date
from sqlmodel import Session

from models import Company, FinancialFact, TaxonomyMapping
from repository import (
    create_company,
    update_company_cik,
    upsert_financial_facts,
    get_financial_facts,
    upsert_taxonomy_mappings,
    get_taxonomy_mappings,
)


def _make_company(db: Session) -> Company:
    return create_company(
        db,
        Company(ticker="AAPL", name="Apple Inc."),
    )


def _make_fact(company_id: int, **overrides) -> FinancialFact:
    defaults = dict(
        company_id=company_id,
        metric="revenue",
        value=100000.0,
        unit="USD",
        end_date=date(2023, 9, 30),
        period_type="FY",
        source="SEC",
    )
    defaults.update(overrides)
    return FinancialFact(**defaults)


def test_update_company_cik(engine, db_init):
    with Session(engine) as db:
        company = _make_company(db)
        updated = update_company_cik(db, company.id, "0000320193")
        assert updated is not None
        assert updated.cik == "0000320193"


def test_update_company_cik_not_found(engine, db_init):
    with Session(engine) as db:
        result = update_company_cik(db, 9999, "0000320193")
        assert result is None


def test_insert_financial_facts(engine, db_init):
    with Session(engine) as db:
        company = _make_company(db)
        facts = [
            _make_fact(company.id),
            _make_fact(company.id, metric="net_income", value=50000.0),
        ]
        inserted, updated = upsert_financial_facts(db, facts)
        assert inserted == 2
        assert updated == 0


def test_upsert_updates_existing_facts(engine, db_init):
    with Session(engine) as db:
        company = _make_company(db)
        facts = [_make_fact(company.id, value=100000.0)]
        upsert_financial_facts(db, facts)

        updated_facts = [_make_fact(company.id, value=200000.0)]
        inserted, updated = upsert_financial_facts(db, updated_facts)
        assert inserted == 0
        assert updated == 1

        results = get_financial_facts(db, company.id)
        assert len(results) == 1
        assert results[0].value == 200000.0


def test_get_financial_facts_filter_metric(engine, db_init):
    with Session(engine) as db:
        company = _make_company(db)
        facts = [
            _make_fact(company.id, metric="revenue"),
            _make_fact(company.id, metric="net_income", value=50000.0),
        ]
        upsert_financial_facts(db, facts)

        revenue = get_financial_facts(db, company.id, metric="revenue")
        assert len(revenue) == 1
        assert revenue[0].metric == "revenue"


def test_get_financial_facts_filter_period_type(engine, db_init):
    with Session(engine) as db:
        company = _make_company(db)
        facts = [
            _make_fact(company.id, period_type="FY"),
            _make_fact(
                company.id,
                period_type="Q1",
                end_date=date(2023, 12, 30),
                value=30000.0,
            ),
        ]
        upsert_financial_facts(db, facts)

        quarterly = get_financial_facts(db, company.id, period_type="Q1")
        assert len(quarterly) == 1
        assert quarterly[0].period_type == "Q1"


def test_upsert_taxonomy_mappings(engine, db_init):
    with Session(engine) as db:
        mappings = [
            {"xbrl_tag": "Revenues", "metric": "revenue", "description": "Total revenue"},
            {"xbrl_tag": "NetIncomeLoss", "metric": "net_income", "description": "Net income"},
        ]
        count = upsert_taxonomy_mappings(db, mappings)
        assert count == 2

        results = get_taxonomy_mappings(db)
        assert len(results) == 2


def test_upsert_taxonomy_mappings_updates(engine, db_init):
    with Session(engine) as db:
        mappings = [
            {"xbrl_tag": "Revenues", "metric": "revenue", "description": "Old"},
        ]
        upsert_taxonomy_mappings(db, mappings)

        updated = [
            {"xbrl_tag": "Revenues", "metric": "revenue", "description": "New"},
        ]
        upsert_taxonomy_mappings(db, updated)

        results = get_taxonomy_mappings(db, metric="revenue")
        assert len(results) == 1
        assert results[0].description == "New"
