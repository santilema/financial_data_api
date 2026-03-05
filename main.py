import logging
from fastapi import FastAPI, Depends, Query
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from contextlib import asynccontextmanager
from sqlmodel import SQLModel, Session
from typing import List, Literal
from datetime import date, timedelta

from database import engine, get_session
from models import Company, DailyPrice, FinancialFact, TaxonomyMapping
import repository
import schemas
from schemas import ApiException
from services import yfinance_client
from services.edgar_pipeline import seed_taxonomy, ingest_company_financials
from services.edgar_client import EdgarClientError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # code in here runs ONCE before app starts
    logger.info("Starting up")
    logger.info("Creating database tables")
    # Find all classes that inherit from SQLModel (e.g. Company)
    SQLModel.metadata.create_all(engine)
    logger.info("Tables created")

    with Session(engine) as db:
        count = seed_taxonomy(db)
        logger.info("Seeded %d taxonomy mappings", count)

    yield
    # code in here runs ONCE when the app is shutting down
    logger.info("Shutting down")


app = FastAPI(lifespan=lifespan)


@app.exception_handler(ApiException)
async def api_exception_handler(request, exc: ApiException):
    body = {"error": exc.error, "message": exc.detail}
    body.update({k: v for k, v in exc.ctx.items() if v is not None})
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    first = exc.errors()[0] if exc.errors() else {}
    return JSONResponse(status_code=422, content={
        "error": "invalid_params",
        "message": "Invalid request parameters",
        "field": ".".join(str(x) for x in first.get("loc", [])),
        "detail": first.get("msg", ""),
    })


@app.get("/")
def read_root():
    return {"message": "Hello world :D"}


@app.post("/companies/{ticker}", response_model=Company)
async def add_new_company(ticker: str, db: Session = Depends(get_session)):
    """
    Fetches ~20 years of data from yfinance and stores it in the database.
    """
    logger.info(f"Request received to add company {ticker}")
    # check if already exists
    db_company = repository.get_company_by_ticker(db, ticker=ticker)
    if db_company:
        logger.warning(f"Company {ticker} already exists.")
        raise ApiException(status_code=400, error="already_exists", message=f"Company {ticker} already exists.", ticker=ticker)

    # fetch from client
    try:
        logger.info(f"Fetching data for {ticker}")
        company, prices = await yfinance_client.fetch_daily_data(ticker)
    except yfinance_client.YahooFinanceError as e:
        logger.error(f"Failed to fetch data for {ticker}: {e}")
        raise ApiException(status_code=400, error="ingestion_failed", message=f"Failed to fetch data for {ticker}.", ticker=ticker, detail=str(e))

    # store in db
    db_company = repository.create_company(db, company=company)
    for price in prices:
        if not db_company.id:
            logger.error(f"Failed to retrieve ID for company {ticker}")
            raise ApiException(status_code=500, error="internal_error", message="Something went wrong when retrieving new company id.")
        price.company_id = db_company.id

    repository.save_daily_prices(db, prices=prices)
    db.refresh(db_company)
    logger.info(f"Company {ticker} saved successfully")
    return db_company


@app.get("/companies", response_model=List[Company])
async def get_all_companies(db: Session = Depends(get_session)):
    """
    Gets a list with all available companies.
    """
    logger.info("Request received to get all companies")
    db_companies = repository.get_all_companies(db)
    return db_companies


@app.get("/prices/{ticker}", response_model=List[DailyPrice])
async def get_prices_for_ticker(
    ticker: str,
    from_date: date | None = Query(None, alias="from"),
    until_date: date | None = Query(None, alias="until"),
    db: Session = Depends(get_session),
):
    """
    Gets stored daily prices for a given company, optionally
    filtered by date range.
    """
    logger.info(f"Request received to get prices for {ticker}")
    db_company = repository.get_company_by_ticker(db, ticker=ticker)
    if not db_company:
        raise ApiException(status_code=404, error="not_found", message=f"Company {ticker} not found.", ticker=ticker)
    if not db_company.id:
        raise ApiException(status_code=500, error="internal_error", message="Something went wrong when retrieving company id.")

    # Default bounds to full history if one side is missing
    if from_date is None:
        from_date = repository.get_earliest_date_for_company(
            db, company_id=db_company.id
        )
    if until_date is None:
        until_date = repository.get_latest_date_for_company(
            db, company_id=db_company.id
        )

    if from_date and until_date and from_date > until_date:
        raise ApiException(status_code=400, error="invalid_params", message="'from' must be on or before 'until'.", field="from")

    prices = repository.get_prices_for_company(
        db,
        company_id=db_company.id,
        start_date=from_date,
        end_date=until_date,
    )
    logger.info(f"Prices for {ticker} retrieved successfully")
    return prices


@app.delete("/companies/{ticker}", response_model=dict)
@app.delete("/prices/{ticker}", response_model=dict)
async def delete_company(ticker: str, db: Session = Depends(get_session)):
    """
    Remove ticker and all its price data from the database.
    """
    logger.info(f"Request received to delete company {ticker}")
    db_company = repository.get_company_by_ticker(db, ticker=ticker)
    if not db_company:
        raise ApiException(status_code=404, error="not_found", message=f"Company {ticker} not found.", ticker=ticker)
    if not db_company.id:
        raise ApiException(status_code=500, error="internal_error", message="Something went wrong when retrieving company id.")

    prices_deleted, companies_deleted = repository.delete_company_and_prices(
        db, company_id=db_company.id
    )

    if companies_deleted == 0:
        logger.error(f"Failed to delete company {db_company.ticker} after found.")
        raise ApiException(status_code=500, error="internal_error", message=f"Failed to delete company {db_company.ticker}.")
    logger.info(f"Company {db_company.ticker} deleted successfully")

    return {
        "message": f"Company {db_company.ticker} deleted with {prices_deleted} price rows removed.",
        "prices_deleted": prices_deleted,
        "company_deleted": companies_deleted,
    }


@app.post("/prices/{ticker}/sync", response_model=dict)
async def sync_latest_prices(ticker: str, db: Session = Depends(get_session)):
    """
    This endpoint finds the latest data in the database
    and fetches everything new since then.
    """
    logger.info(f"Request received to sync latest prices for {ticker}")
    # find the company
    db_company = repository.get_company_by_ticker(db, ticker)
    if not db_company:
        raise ApiException(status_code=404, error="not_found", message=f"Company {ticker} not found.", ticker=ticker)
    if not db_company.id:
        raise ApiException(status_code=500, error="internal_error", message="Something went wrong when retrieving company id.")

    # find latest date in db
    latest_date = repository.get_latest_date_for_company(db, company_id=db_company.id)

    if not latest_date:
        logger.error("No price data found to sync.")
        raise ApiException(status_code=400, error="invalid_params", message="No price data found to sync.", ticker=ticker)

    # is already up-to-date?
    if latest_date >= (date.today() - timedelta(days=1)):
        logger.info("Data already up-to-date.")
        return {"message": "Data already up-to-date."}

    # fetch only new data
    try:
        new_prices = await yfinance_client.fetch_daily_data_since(
            ticker=ticker, start_date=latest_date + timedelta(days=1)
        )
    except yfinance_client.YahooFinanceError as e:
        logger.error(f"Failed to fetch new data: {e}")
        raise ApiException(status_code=500, error="internal_error", message="Failed to fetch new data.", detail=str(e))

    if not new_prices:
        logger.info("Data is already up-to-date")
        return {"message": "Data is already up-to-date"}

    # save new prices
    for price in new_prices:
        price.company_id = db_company.id

    repository.save_daily_prices(db, prices=new_prices)
    logger.info(f"Sync complete. {len(new_prices)} new records added.")
    return {"message": f"Sync complete. {len(new_prices)} new records added."}


@app.post("/companies/{ticker}/financials")
async def ingest_financials(ticker: str, db: Session = Depends(get_session)):
    """
    Triggers SEC EDGAR ingestion for a company.
    """
    logger.info(f"Request received to ingest financials for {ticker}")
    db_company = repository.get_company_by_ticker(db, ticker=ticker)
    if not db_company:
        raise ApiException(status_code=404, error="not_found", message=f"Company {ticker} not found.", ticker=ticker)

    try:
        result = await ingest_company_financials(db, ticker)
    except EdgarClientError as e:
        logger.error(f"EDGAR ingestion failed for {ticker}: {e}")
        raise ApiException(status_code=400, error="ingestion_failed", message=f"EDGAR ingestion failed for {ticker}.", ticker=ticker, detail=str(e))

    logger.info(f"Ingestion complete for {ticker}")
    return result


@app.get("/companies/{ticker}/financials")
async def get_financials(
    ticker: str,
    metric: str | None = Query(None),
    period_type: str | None = Query(None),
    format: str = Query("minimal", pattern="^(minimal|standard|verbose)$"),
    fields: str | None = Query(None),
    db: Session = Depends(get_session),
):
    """
    Gets stored financial facts for a given company,
    optionally filtered by metric and period type.
    Supports format=minimal|standard|verbose and fields=rev,ni,eps.
    """
    logger.info(f"Request received to get financials for {ticker}")
    db_company = repository.get_company_by_ticker(db, ticker=ticker)
    if not db_company:
        raise ApiException(status_code=404, error="not_found", message=f"Company {ticker} not found.", ticker=ticker)
    if not db_company.id:
        raise ApiException(status_code=500, error="internal_error", message="Something went wrong when retrieving company id.")

    facts = repository.get_financial_facts(db, db_company.id, metric, period_type)
    logger.info(f"Financials for {ticker} retrieved successfully")

    field_list = fields.split(",") if fields else None
    return schemas.transform_financials_by_period(
        facts,
        format=format,  # type: ignore[arg-type]
        fields=field_list,
        ticker=ticker,
    )


@app.get("/taxonomy", response_model=List[TaxonomyMapping])
async def get_taxonomy(
    metric: str | None = Query(None),
    db: Session = Depends(get_session),
):
    """
    Lists taxonomy mappings, optionally filtered by metric.
    """
    logger.info("Request received to get taxonomy mappings")
    mappings = repository.get_taxonomy_mappings(db, metric)
    return mappings
