import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, select

from models import Company, FinancialFact, TaxonomyMapping
from services.edgar_client import EdgarClientError
from services.edgar_pipeline import ingest_company_financials, seed_taxonomy

FIXTURES = Path(__file__).parent / "fixtures"


def _load_aapl_facts() -> dict:
    with open(FIXTURES / "aapl_companyfacts.json") as f:
        return json.load(f)


def _create_company(
    db: Session, ticker: str = "AAPL", cik: str | None = None
) -> Company:
    company = Company(ticker=ticker, name="Apple Inc.", cik=cik)
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def test_ingest_success(engine, db_init):
    with Session(engine, expire_on_commit=False) as db:
        company = _create_company(db)

        with (
            patch(
                "services.edgar_pipeline.edgar_client.resolve_cik",
                new_callable=AsyncMock,
                return_value="0000320193",
            ),
            patch(
                "services.edgar_pipeline.edgar_client.fetch_company_facts",
                new_callable=AsyncMock,
                return_value=_load_aapl_facts(),
            ),
        ):
            result = asyncio.run(ingest_company_financials(db, "AAPL"))

        assert result["ticker"] == "AAPL"
        assert result["cik"] == "0000320193"
        assert result["inserted"] > 0

        # Verify facts persisted
        facts = db.exec(
            select(FinancialFact).where(FinancialFact.company_id == company.id)
        ).all()
        assert len(facts) > 0

        # Verify CIK was saved on company
        db.refresh(company)
        assert company.cik == "0000320193"


def test_ingest_company_not_found(engine, db_init):
    with Session(engine, expire_on_commit=False) as db:
        with pytest.raises(EdgarClientError, match="not found"):
            asyncio.run(ingest_company_financials(db, "NONEXISTENT"))


def test_ingest_sets_cik_only_when_missing(engine, db_init):
    with Session(engine, expire_on_commit=False) as db:
        _create_company(db, cik=None)

        mock_resolve = AsyncMock(return_value="0000320193")
        mock_facts = AsyncMock(return_value=_load_aapl_facts())

        with (
            patch("services.edgar_pipeline.edgar_client.resolve_cik", mock_resolve),
            patch(
                "services.edgar_pipeline.edgar_client.fetch_company_facts", mock_facts
            ),
            patch(
                "services.edgar_pipeline.repository.update_company_cik"
            ) as mock_update_cik,
        ):
            # First call - CIK is None, should update
            asyncio.run(ingest_company_financials(db, "AAPL"))
            assert mock_update_cik.call_count == 1

            # Manually set CIK so second call sees it
            company = db.exec(select(Company).where(Company.ticker == "AAPL")).first()
            company.cik = "0000320193"
            db.add(company)
            db.commit()

            # Second call - CIK already set, should not update
            mock_update_cik.reset_mock()
            asyncio.run(ingest_company_financials(db, "AAPL"))
            assert mock_update_cik.call_count == 0


def test_ingest_returns_counts(engine, db_init):
    with Session(engine, expire_on_commit=False) as db:
        _create_company(db)

        with (
            patch(
                "services.edgar_pipeline.edgar_client.resolve_cik",
                new_callable=AsyncMock,
                return_value="0000320193",
            ),
            patch(
                "services.edgar_pipeline.edgar_client.fetch_company_facts",
                new_callable=AsyncMock,
                return_value=_load_aapl_facts(),
            ),
        ):
            result = asyncio.run(ingest_company_financials(db, "AAPL"))

        assert "inserted" in result
        assert "updated" in result
        assert isinstance(result["inserted"], int)
        assert isinstance(result["updated"], int)
        assert result["inserted"] + result["updated"] > 0


def test_seed_taxonomy(engine, db_init):
    with Session(engine, expire_on_commit=False) as db:
        count = seed_taxonomy(db)
        assert count > 0

        mappings = db.exec(select(TaxonomyMapping)).all()
        assert len(mappings) == count

        # Idempotency - second call returns same count, no duplicates
        count2 = seed_taxonomy(db)
        assert count2 == count

        mappings2 = db.exec(select(TaxonomyMapping)).all()
        assert len(mappings2) == count
