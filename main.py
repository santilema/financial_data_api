import logging
from fastapi import FastAPI, Depends, Query
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from contextlib import asynccontextmanager
from sqlmodel import SQLModel, Session
from typing import List, Literal
from datetime import date, timedelta

from database import engine, get_session
from admin import router as admin_router
from models import Company, DailyPrice, FinancialFact, TaxonomyMapping
import repository
import schemas
from schemas import ApiException
from services import yfinance_client
from services.edgar_pipeline import seed_taxonomy, ingest_company_financials
from services.edgar_client import EdgarClientError
from services.ratios import build_ratio_inputs_from_facts, compute_ratios

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


app = FastAPI(
    lifespan=lifespan,
    title="FinDataAI",
    description=(
        "Token-efficient financial data API designed for LLM agent consumption. "
        "Combines SEC EDGAR fundamental data with yfinance price history. "
        "All financial endpoints support `?format=minimal|standard|verbose` "
        "and `?fields=` for field selection."
    ),
    version="0.5.0",
)
app.include_router(admin_router)


@app.exception_handler(ApiException)
async def api_exception_handler(request, exc: ApiException):
    body = {"error": exc.error, "message": exc.detail}
    body.update({k: v for k, v in exc.ctx.items() if v is not None})
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    first = exc.errors()[0] if exc.errors() else {}
    return JSONResponse(
        status_code=422,
        content={
            "error": "invalid_params",
            "message": "Invalid request parameters",
            "field": ".".join(str(x) for x in first.get("loc", [])),
            "detail": first.get("msg", ""),
        },
    )


@app.get("/")
def read_root():
    return {"message": "Hello world :D"}


@app.post("/companies/{ticker}", response_model=Company, tags=["Companies"])
async def add_new_company(ticker: str, db: Session = Depends(get_session)):
    """
    Register a new company and fetch its full price history from yfinance (~20 years).
    Returns the created company record. Raises 400 if the ticker already exists.
    """
    logger.info(f"Request received to add company {ticker}")
    # check if already exists
    db_company = repository.get_company_by_ticker(db, ticker=ticker)
    if db_company:
        logger.warning(f"Company {ticker} already exists.")
        raise ApiException(
            status_code=400,
            error="already_exists",
            message=f"Company {ticker} already exists.",
            ticker=ticker,
        )

    # fetch from client
    try:
        logger.info(f"Fetching data for {ticker}")
        company, prices = await yfinance_client.fetch_daily_data(ticker)
    except yfinance_client.YahooFinanceError as e:
        logger.error(f"Failed to fetch data for {ticker}: {e}")
        raise ApiException(
            status_code=400,
            error="ingestion_failed",
            message=f"Failed to fetch data for {ticker}.",
            ticker=ticker,
            detail=str(e),
        )

    # store in db
    db_company = repository.create_company(db, company=company)
    for price in prices:
        if not db_company.id:
            logger.error(f"Failed to retrieve ID for company {ticker}")
            raise ApiException(
                status_code=500,
                error="internal_error",
                message="Something went wrong when retrieving new company id.",
            )
        price.company_id = db_company.id

    repository.save_daily_prices(db, prices=prices)
    db.refresh(db_company)
    logger.info(f"Company {ticker} saved successfully")
    return db_company


@app.get("/companies", response_model=List[Company], tags=["Companies"])
async def get_all_companies(db: Session = Depends(get_session)):
    """
    Returns all companies registered in the database.
    """
    logger.info("Request received to get all companies")
    db_companies = repository.get_all_companies(db)
    return db_companies


@app.get("/companies/{ticker}", tags=["Companies"])
async def get_company_profile(
    ticker: str,
    format: str = Query(
        "minimal",
        pattern="^(minimal|standard|verbose)$",
        description="Response verbosity. minimal=short keys (agent-optimized), standard=full names, verbose=with descriptions.",
    ),
    db: Session = Depends(get_session),
):
    """
    Returns a compact company profile: metadata, latest FY financials, and key ratios.
    Includes price data if available. Returns 404 if the ticker is not registered.
    """
    logger.info(f"Request received to get profile for {ticker}")
    db_company = repository.get_company_by_ticker(db, ticker=ticker)
    if not db_company:
        raise ApiException(
            status_code=404,
            error="not_found",
            message=f"Company {ticker} not found.",
            ticker=ticker,
        )
    if not db_company.id:
        raise ApiException(
            status_code=500,
            error="internal_error",
            message="Something went wrong when retrieving company id.",
        )

    facts = repository.get_latest_fy_facts(db, db_company.id)
    price_row = repository.get_latest_price(db, db_company.id)

    if facts:
        price = price_row.close if price_row else None
        price_date = price_row.date if price_row else None
        inputs = build_ratio_inputs_from_facts(
            facts, price=price, price_date=price_date
        )
        ratios = compute_ratios(inputs)
    else:
        inputs, ratios = None, None

    return schemas.transform_company_profile(
        db_company, ratios, inputs, price_row, ticker, format  # type: ignore[arg-type]
    )


@app.get("/compare", tags=["Compare"])
async def compare_companies(
    tickers: str = Query(
        ...,
        description="Comma-separated ticker list, max 10.",
    ),
    metrics: str | None = Query(
        None,
        description="Comma-separated minimal-key fields to include, e.g. `rev,ni,gm`. Omit for all.",
    ),
    format: str = Query(
        "minimal",
        pattern="^(minimal|standard|verbose)$",
        description="Response verbosity. minimal=short keys (agent-optimized), standard=full names, verbose=with descriptions.",
    ),
    db: Session = Depends(get_session),
):
    """
    Returns a side-by-side metric matrix for up to 10 tickers.
    Tickers not found in the database appear with all-null values and are listed in `_meta.not_found`.
    Use `?metrics=rev,ni,pe` to limit the columns returned.
    """
    raw = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    ticker_list = list(dict.fromkeys(raw))

    if not ticker_list:
        raise ApiException(
            status_code=422,
            error="invalid_params",
            message="At least one ticker is required.",
            field="tickers",
        )
    if len(ticker_list) > 10:
        raise ApiException(
            status_code=422,
            error="invalid_params",
            message="At most 10 tickers are allowed.",
            field="tickers",
        )

    ticker_results = []
    for ticker in ticker_list:
        company = repository.get_company_by_ticker(db, ticker=ticker)
        if not company or not company.id:
            ticker_results.append(
                {
                    "ticker": ticker,
                    "found": False,
                    "facts": [],
                    "ratios": None,
                    "inputs": None,
                }
            )
            continue

        facts = repository.get_latest_fy_facts(db, company.id)
        price_row = repository.get_latest_price(db, company.id)

        if facts:
            inputs = build_ratio_inputs_from_facts(
                facts,
                price=price_row.close if price_row else None,
                price_date=price_row.date if price_row else None,
            )
            ratios = compute_ratios(inputs)
        else:
            inputs, ratios = None, None

        ticker_results.append(
            {
                "ticker": ticker,
                "found": True,
                "facts": facts,
                "ratios": ratios,
                "inputs": inputs,
            }
        )

    metric_list = metrics.split(",") if metrics else None
    return schemas.transform_comparison(ticker_results, metric_list, format)


@app.get("/search", tags=["Search"])
async def search_companies(
    q: str | None = Query(
        None,
        description="Search by ticker or company name (case-insensitive partial match).",
    ),
    sector: str | None = Query(
        None,
        description="Filter by sector (case-insensitive exact match).",
    ),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_session),
):
    """
    Search companies by ticker or name. Returns ticker, name, sector, plus availability
    indicators: `has_financials` (bool) and `latest_fy` (int year or null) show whether
    EDGAR data has been ingested. Exact ticker matches rank first.
    """
    companies = repository.search_companies(db, q=q, sector=sector, limit=limit)
    ids = [c.id for c in companies if c.id]
    availability = repository.get_companies_financials_availability(db, ids)
    return [
        {
            "ticker": c.ticker,
            "name": c.name,
            "sector": c.sector,
            "has_financials": c.id in availability,
            "latest_fy": availability.get(c.id),
        }
        for c in companies
    ]


@app.get("/prices/{ticker}", response_model=List[DailyPrice], tags=["Prices"])
async def get_prices_for_ticker(
    ticker: str,
    from_date: date | None = Query(None, alias="from"),
    until_date: date | None = Query(None, alias="until"),
    db: Session = Depends(get_session),
):
    """
    Returns stored daily OHLCV prices for a company, optionally filtered by date range.
    Defaults to the full available history when no date bounds are provided.
    """
    logger.info(f"Request received to get prices for {ticker}")
    db_company = repository.get_company_by_ticker(db, ticker=ticker)
    if not db_company:
        raise ApiException(
            status_code=404,
            error="not_found",
            message=f"Company {ticker} not found.",
            ticker=ticker,
        )
    if not db_company.id:
        raise ApiException(
            status_code=500,
            error="internal_error",
            message="Something went wrong when retrieving company id.",
        )

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
        raise ApiException(
            status_code=400,
            error="invalid_params",
            message="'from' must be on or before 'until'.",
            field="from",
        )

    prices = repository.get_prices_for_company(
        db,
        company_id=db_company.id,
        start_date=from_date,
        end_date=until_date,
    )
    logger.info(f"Prices for {ticker} retrieved successfully")
    return prices


@app.delete("/companies/{ticker}", response_model=dict, tags=["Companies"])
@app.delete("/prices/{ticker}", response_model=dict, tags=["Prices"])
async def delete_company(ticker: str, db: Session = Depends(get_session)):
    """
    Remove a company and all its associated price and financial data from the database.
    """
    logger.info(f"Request received to delete company {ticker}")
    db_company = repository.get_company_by_ticker(db, ticker=ticker)
    if not db_company:
        raise ApiException(
            status_code=404,
            error="not_found",
            message=f"Company {ticker} not found.",
            ticker=ticker,
        )
    if not db_company.id:
        raise ApiException(
            status_code=500,
            error="internal_error",
            message="Something went wrong when retrieving company id.",
        )

    prices_deleted, companies_deleted = repository.delete_company_and_prices(
        db, company_id=db_company.id
    )

    if companies_deleted == 0:
        logger.error(f"Failed to delete company {db_company.ticker} after found.")
        raise ApiException(
            status_code=500,
            error="internal_error",
            message=f"Failed to delete company {db_company.ticker}.",
        )
    logger.info(f"Company {db_company.ticker} deleted successfully")

    return {
        "message": f"Company {db_company.ticker} deleted with {prices_deleted} price rows removed.",
        "prices_deleted": prices_deleted,
        "company_deleted": companies_deleted,
    }


@app.post("/prices/{ticker}/sync", response_model=dict, tags=["Prices"])
async def sync_latest_prices(ticker: str, db: Session = Depends(get_session)):
    """
    Incrementally sync price data. Finds the latest stored date and fetches
    only new trading days since then. Returns immediately if already up-to-date.
    """
    logger.info(f"Request received to sync latest prices for {ticker}")
    # find the company
    db_company = repository.get_company_by_ticker(db, ticker)
    if not db_company:
        raise ApiException(
            status_code=404,
            error="not_found",
            message=f"Company {ticker} not found.",
            ticker=ticker,
        )
    if not db_company.id:
        raise ApiException(
            status_code=500,
            error="internal_error",
            message="Something went wrong when retrieving company id.",
        )

    # find latest date in db
    latest_date = repository.get_latest_date_for_company(db, company_id=db_company.id)

    if not latest_date:
        logger.error("No price data found to sync.")
        raise ApiException(
            status_code=400,
            error="invalid_params",
            message="No price data found to sync.",
            ticker=ticker,
        )

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
        raise ApiException(
            status_code=500,
            error="internal_error",
            message="Failed to fetch new data.",
            detail=str(e),
        )

    if not new_prices:
        logger.info("Data is already up-to-date")
        return {"message": "Data is already up-to-date"}

    # save new prices
    for price in new_prices:
        price.company_id = db_company.id

    repository.save_daily_prices(db, prices=new_prices)
    logger.info(f"Sync complete. {len(new_prices)} new records added.")
    return {"message": f"Sync complete. {len(new_prices)} new records added."}


@app.post("/companies/{ticker}/financials", tags=["Financials"])
async def ingest_financials(ticker: str, db: Session = Depends(get_session)):
    """
    Triggers SEC EDGAR ingestion for the company. Resolves the CIK, fetches 10-K/10-Q
    filings, and upserts financial facts into the database. Returns insert/update counts.
    """
    logger.info(f"Request received to ingest financials for {ticker}")
    db_company = repository.get_company_by_ticker(db, ticker=ticker)
    if not db_company:
        raise ApiException(
            status_code=404,
            error="not_found",
            message=f"Company {ticker} not found.",
            ticker=ticker,
        )

    try:
        result = await ingest_company_financials(db, ticker)
    except EdgarClientError as e:
        logger.error(f"EDGAR ingestion failed for {ticker}: {e}")
        raise ApiException(
            status_code=400,
            error="ingestion_failed",
            message=f"EDGAR ingestion failed for {ticker}.",
            ticker=ticker,
            detail=str(e),
        )

    logger.info(f"Ingestion complete for {ticker}")
    return result


@app.get("/companies/{ticker}/financials", tags=["Financials"])
async def get_financials(
    ticker: str,
    metric: str | None = Query(None),
    period_type: str | None = Query(None),
    format: str = Query(
        "minimal",
        pattern="^(minimal|standard|verbose)$",
        description="Response verbosity. minimal=short keys (agent-optimized), standard=full names, verbose=with descriptions.",
    ),
    fields: str | None = Query(
        None,
        description="Comma-separated minimal-key fields to include, e.g. `rev,ni,eps`. Omit for all.",
    ),
    db: Session = Depends(get_session),
):
    """
    Returns stored financial facts grouped by period (FY/Q1-Q4). Each period group
    includes a `_meta` freshness object with filing date and data age. Use `?fields=rev,ni`
    to limit fields and `?period_type=FY` for annual-only data.
    """
    logger.info(f"Request received to get financials for {ticker}")
    db_company = repository.get_company_by_ticker(db, ticker=ticker)
    if not db_company:
        raise ApiException(
            status_code=404,
            error="not_found",
            message=f"Company {ticker} not found.",
            ticker=ticker,
        )
    if not db_company.id:
        raise ApiException(
            status_code=500,
            error="internal_error",
            message="Something went wrong when retrieving company id.",
        )

    facts = repository.get_financial_facts(db, db_company.id, metric, period_type)
    logger.info(f"Financials for {ticker} retrieved successfully")

    field_list = fields.split(",") if fields else None
    return schemas.transform_financials_by_period(
        facts,
        format=format,  # type: ignore[arg-type]
        fields=field_list,
        ticker=ticker,
    )


@app.get("/companies/{ticker}/ratios", tags=["Financials"])
async def get_ratios(
    ticker: str,
    format: str = Query(
        "minimal",
        pattern="^(minimal|standard|verbose)$",
        description="Response verbosity. minimal=short keys (agent-optimized), standard=full names, verbose=with descriptions.",
    ),
    fields: str | None = Query(
        None,
        description="Comma-separated minimal-key fields to include, e.g. `gm,pe,de`. Omit for all.",
    ),
    db: Session = Depends(get_session),
):
    """
    Returns computed financial ratios for the most recent fiscal year.
    Includes margins, EBITDA, D/E, ROE, and price-based ratios (PE, PB) when price
    data is available. Returns 404 if no FY financial data has been ingested.
    """
    logger.info(f"Request received to get ratios for {ticker}")
    db_company = repository.get_company_by_ticker(db, ticker=ticker)
    if not db_company:
        raise ApiException(
            status_code=404,
            error="not_found",
            message=f"Company {ticker} not found.",
            ticker=ticker,
        )
    if not db_company.id:
        raise ApiException(
            status_code=500,
            error="internal_error",
            message="Something went wrong when retrieving company id.",
        )

    facts = repository.get_latest_fy_facts(db, db_company.id)
    if not facts:
        raise ApiException(
            status_code=404,
            error="not_found",
            message=f"No annual (FY) financial data found for {ticker}.",
            ticker=ticker,
        )

    price_row = repository.get_latest_price(db, db_company.id)
    price = price_row.close if price_row else None
    price_date = price_row.date if price_row else None

    inputs = build_ratio_inputs_from_facts(facts, price=price, price_date=price_date)
    ratios = compute_ratios(inputs)

    field_list = fields.split(",") if fields else None
    return schemas.transform_ratios(ratios, inputs, ticker, format=format, fields=field_list)  # type: ignore[arg-type]


@app.get("/companies/{ticker}/trend", tags=["Financials"])
async def get_trend(
    ticker: str,
    metrics: str | None = Query(
        None,
        description="Comma-separated minimal-key fields to include, e.g. `rev,ni,gm`. Omit for all.",
    ),
    periods: int = Query(
        5,
        ge=1,
        le=20,
        description="Number of fiscal years to return (most recent N, chronological order).",
    ),
    format: str = Query(
        "minimal",
        pattern="^(minimal|standard|verbose)$",
        description="Response verbosity. minimal=short keys (agent-optimized), standard=full names, verbose=with descriptions.",
    ),
    db: Session = Depends(get_session),
):
    """
    Returns multi-year trend data for a company. Each period row contains raw financial
    metrics and computed ratios (margins, EBITDA, D/E, ROE). PE and PB are always null
    here - historical price data per fiscal year is not available. Periods are sorted
    oldest to newest. Returns 200 with empty periods if the company exists but has no
    FY data yet. Returns 404 if the company is not registered.
    """
    db_company = repository.get_company_by_ticker(db, ticker=ticker)
    if not db_company:
        raise ApiException(
            status_code=404,
            error="not_found",
            message=f"Company {ticker} not found.",
            ticker=ticker,
        )
    if not db_company.id:
        raise ApiException(
            status_code=500,
            error="internal_error",
            message="Something went wrong when retrieving company id.",
        )

    facts = repository.get_financial_facts(db, db_company.id, period_type="FY")
    metric_list = metrics.split(",") if metrics else None
    return schemas.transform_trend(facts, ticker, format, metric_list, periods)  # type: ignore[arg-type]


@app.get("/taxonomy", response_model=List[TaxonomyMapping], tags=["Taxonomy"])
async def get_taxonomy(
    metric: str | None = Query(None),
    db: Session = Depends(get_session),
):
    """
    Lists XBRL taxonomy mappings (tag to metric name). Use `?metric=revenue` to filter.
    """
    logger.info("Request received to get taxonomy mappings")
    mappings = repository.get_taxonomy_mappings(db, metric)
    return mappings
