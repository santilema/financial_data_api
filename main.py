import logging
from fastapi import FastAPI, Depends, HTTPException, Query
from contextlib import asynccontextmanager
from sqlmodel import SQLModel, Session
from typing import List
from datetime import date, timedelta

from database import engine, get_session
from models import Instrument, DailyPrice
import repository
from services import yfinance_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # code in here runs ONCE before app starts
    logger.info("Starting up")
    logger.info("Creating database tables")
    # Find all classes that inherit from SQLModel (e.g. Instrument)
    SQLModel.metadata.create_all(engine)
    logger.info("Tables created")

    yield
    # code in here runs ONCE when the app is shutting down
    logger.info("Shutting down")


app = FastAPI(lifespan=lifespan)


@app.get("/")
def read_root():
    return {"message": "Hello world :D"}


@app.post("/instruments/{ticker}", response_model=Instrument)
async def add_new_instrument(ticker: str, db: Session = Depends(get_session)):
    """
    Fetches ~20 years of data from yfinance and stores it in the database.
    """
    logger.info(f"Request received to add instrument {ticker}")
    # check if already exists
    db_instrument = repository.get_instrument_by_ticker(db, ticker=ticker)
    if db_instrument:
        logger.warning(f"Instrument {ticker} already exists.")
        raise HTTPException(
            status_code=400, detail=f"Instrument {ticker} already exists."
        )

    # fetch from client
    try:
        logger.info(f"Fetching data for {ticker}")
        instrument, prices = await yfinance_client.fetch_daily_data(ticker)
    except yfinance_client.YahooFinanceError as e:
        logger.error(f"Failed to fetch data for {ticker}: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    # store in db
    db_instrument = repository.create_instrument(db, instrument=instrument)
    for price in prices:
        if not db_instrument.id:
            logger.error(f"Failed to retrieve ID for instrument {ticker}")
            raise HTTPException(
                500, detail="Something went wrong when retrieving new instrument id."
            )
        price.instrument_id = db_instrument.id

    repository.save_daily_prices(db, prices=prices)
    db.refresh(db_instrument)
    logger.info(f"Instrument {ticker} saved successfully")
    return db_instrument


@app.get("/instruments", response_model=List[Instrument])
async def get_all_instruments(db: Session = Depends(get_session)):
    """
    Gets a list with all available instruments.
    """
    logger.info("Request received to get all instruments")
    db_instruments = repository.get_all_instruments(db)
    return db_instruments


@app.get("/prices/{ticker}", response_model=List[DailyPrice])
async def get_prices_for_ticker(
    ticker: str,
    from_date: date | None = Query(None, alias="from"),
    until_date: date | None = Query(None, alias="until"),
    db: Session = Depends(get_session),
):
    """
    Gets stored daily prices for a given instrument, optionally
    filtered by date range.
    """
    logger.info(f"Request received to get prices for {ticker}")
    db_instrument = repository.get_instrument_by_ticker(db, ticker=ticker)
    if not db_instrument:
        raise HTTPException(status_code=404, detail=f"Instrument {ticker} not found.")
    if not db_instrument.id:
        raise HTTPException(
            status_code=500,
            detail="Something went wrong when retrieving instrument id.",
        )

    # Default bounds to full history if one side is missing
    if from_date is None:
        from_date = repository.get_earliest_date_for_instrument(
            db, instrument_id=db_instrument.id
        )
    if until_date is None:
        until_date = repository.get_latest_date_for_instrument(
            db, instrument_id=db_instrument.id
        )

    if from_date and until_date and from_date > until_date:
        raise HTTPException(
            status_code=400, detail="'from' must be on or before 'until'."
        )

    prices = repository.get_prices_for_instrument(
        db,
        instrument_id=db_instrument.id,
        start_date=from_date,
        end_date=until_date,
    )
    logger.info(f"Prices for {ticker} retrieved successfully")
    return prices


@app.delete("/instruments/{ticker}", response_model=dict)
@app.delete("/prices/{ticker}", response_model=dict)
async def delete_instrument(ticker: str, db: Session = Depends(get_session)):
    """
    Remove ticker and all its price data from the database.
    """
    logger.info(f"Request received to delete instrument {ticker}")
    db_instrument = repository.get_instrument_by_ticker(db, ticker=ticker)
    if not db_instrument:
        raise HTTPException(status_code=404, detail=f"Instrument {ticker} not found.")
    if not db_instrument.id:
        raise HTTPException(
            status_code=500,
            detail="Something went wrong when retrieving instrument id.",
        )

    prices_deleted, instruments_deleted = repository.delete_instrument_and_prices(
        db, instrument_id=db_instrument.id
    )

    if instruments_deleted == 0:
        logger.error(f"Failed to delete instrument {db_instrument.ticker} after found.")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete instrument {db_instrument.ticker}.",
        )
    logger.info(f"Instrument {db_instrument.ticker} deleted successfully")

    return {
        "message": f"Instrument {db_instrument.ticker} deleted with {prices_deleted} price rows removed.",
        "prices_deleted": prices_deleted,
        "instrument_deleted": instruments_deleted,
    }


@app.post("/prices/{ticker}/sync", response_model=dict)
async def sync_latest_prices(ticker: str, db: Session = Depends(get_session)):
    """
    This endpoint finds the latest data in the database
    and fetches everything new since then.
    """
    logger.info(f"Request received to sync latest prices for {ticker}")
    # find the instrument
    db_instrument = repository.get_instrument_by_ticker(db, ticker)
    if not db_instrument:
        raise HTTPException(
            status_code=404,
            detail=f"Instrument {ticker} not found. POST to /instruments/{ticker} first.",
        )
    if not db_instrument.id:
        raise HTTPException(
            status_code=500,
            detail="Something went wrong when retrieving instrument id.",
        )

    # find latest date in db
    latest_date = repository.get_latest_date_for_instrument(
        db, instrument_id=db_instrument.id
    )

    if not latest_date:
        logger.error("No price data found to sync.")
        return HTTPException(status_code=400, detail="No price data found to sync.")

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
        raise HTTPException(status_code=500, detail=f"Failed to fetch new data: {e}")

    if not new_prices:
        logger.info("Data is already up-to-date")
        return {"message": "Data is already up-to-date"}

    # save new prices
    for price in new_prices:
        price.instrument_id = db_instrument.id

    repository.save_daily_prices(db, prices=new_prices)
    logger.info(f"Sync complete. {len(new_prices)} new records added.")
    return {"message": f"Sync complete. {len(new_prices)} new records added."}
