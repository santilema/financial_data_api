import logging

from sqlmodel import Session

from services import edgar_client, taxonomy
from services.edgar_client import EdgarClientError
import repository

logger = logging.getLogger(__name__)


async def ingest_company_financials(db: Session, ticker: str) -> dict:
    logger.info("Starting ingestion for ticker=%s", ticker)

    company = repository.get_company_by_ticker(db, ticker)
    if company is None:
        raise EdgarClientError(f"Company {ticker} not found in database")

    logger.info("Resolving CIK for %s", ticker)
    cik = await edgar_client.resolve_cik(ticker)

    if company.cik is None:
        logger.info("Saving CIK %s for company %s", cik, ticker)
        repository.update_company_cik(db, company.id, cik)
        db.refresh(company)

    logger.info("Fetching company facts for CIK %s", cik)
    facts_json = await edgar_client.fetch_company_facts(cik)

    logger.info("Normalizing facts for company_id=%s", company.id)
    facts = taxonomy.normalize_company_facts(company.id, facts_json)

    logger.info("Upserting %d financial facts", len(facts))
    inserted, updated = repository.upsert_financial_facts(db, facts)

    logger.info(
        "Ingestion complete for %s: inserted=%d, updated=%d",
        ticker, inserted, updated,
    )
    return {
        "ticker": ticker,
        "cik": cik,
        "inserted": inserted,
        "updated": updated,
    }


def seed_taxonomy(db: Session) -> int:
    mappings = taxonomy.get_taxonomy_seed_data()
    count = repository.upsert_taxonomy_mappings(db, mappings)
    logger.info("Seeded %d taxonomy mappings", count)
    return count
