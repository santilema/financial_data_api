from fastapi import FastAPI, Depends, HTTPException
from contextlib import asynccontextmanager
from sqlmodel import SQLModel, Session, select
from typing import List
from datetime import date, timedelta

from database import engine, get_session
from models import Instrument, DailyPrice
import repository
from services import yfinance_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    # code in here runs ONCE before app starts
    print("Starting up")
    print("Creating database tables")
    # Find all classes that inherit from SQLModel (e.g. Instrument)
    SQLModel.metadata.create_all(engine)
    print("Tables created")

    yield
    # code in here runs ONCE when the app is shutting down
    print("Shutting down")


app = FastAPI(lifespan=lifespan)


@app.get("/")
def read_root():
    return {"message": "Hello world :D"}


@app.post("/instruments/{ticker}", response_model=Instrument)
async def add_new_instrument(ticker: str, db: Session = Depends(get_session)):
    """
    Fetches ~20 years of data from yfinance and stores it in the database.
    """
    # check if already exists
    db_instrument = repository.get_instrument_by_ticker(db, ticker=ticker)
    print("arrives ", db_instrument)
    if db_instrument:
        raise HTTPException(
            status_code=400, detail=f"Instrument {ticker} already exists."
        )

    # fetch from client
    try:
        instrument, prices = await yfinance_client.fetch_daily_data(ticker)
    except yfinance_client.YahooFinanceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    print("after fetch ", instrument)

    # store in db
    db_instrument = repository.create_instrument(db, instrument=instrument)
    print("after create ", db_instrument.ticker)
    for price in prices:
        price.instrument_id = db_instrument.id

    repository.save_daily_prices(db, prices=prices)
    return db_instrument


@app.get("/prices/{ticker}", response_model=List[DailyPrice])
async def get_prices_for_ticker(ticker: str, db: Session = Depends(get_session)):
    """
    Gets all stored daily prices for a given instrument.
    """
    db_instrument = repository.get_instrument_by_ticker(db, ticker=ticker)
    if not db_instrument:
        raise HTTPException(status_code=404, detail=f"Instrument {ticker} not found.")

    prices = repository.get_prices_for_instrument(db, instrument_id=db_instrument.id)

    return prices


@app.post("/prices/{ticker}/sync", response_model=dict)
async def sync_latest_prices(ticker: str, db: Session = Depends(get_session)):
    """
    This endpoint finds the latest data in the database
    and fetches everything new since then.
    """
    # find the instrument
    db_instrument = repository.get_instrument_by_ticker(db, ticker)
    if not db_instrument:
        raise HTTPException(
            status_code=404,
            detail=f"Instrument {ticker} not found. POST to /instruments/{ticker} first.",
        )

    # find latest date in db
    latest_date = repository.get_latest_date_for_instrument(
        db, instrument_id=db_instrument.id
    )

    if not latest_date:
        # this shouldn't really happen, but just in case
        return HTTPException(status_code=400, detail="No price data found to sync.")

    # is already up-to-date?
    if latest_date >= (date.today() - timedelta(days=1)):
        return {"message": "Data already up-to-date."}

    # fetch only new data
    try:
        new_prices = await yfinance_client.fetch_daily_data_since(
            ticker=ticker, start_date=latest_date + timedelta(days=1)
        )
    except yfinance_client.YahooFinanceError as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch new data: {e}")

    if not new_prices:
        return {"message": "Data is already up-to-date"}

    # save new prices
    for price in new_prices:
        price.instrument_id = db_instrument.id

    repository.save_daily_prices(db, prices=new_prices)

    return {"message": f"Sync complete. {len(new_prices)} new records added."}
