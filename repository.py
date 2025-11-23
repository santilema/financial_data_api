from sqlmodel import Session, select
from models import Instrument, DailyPrice
from typing import List
from datetime import date


def create_instrument(db: Session, instrument: Instrument) -> Instrument:
    """
    Creates a new instrument in the database.
    """
    db.add(instrument)
    db.commit()
    db.refresh(instrument)
    return instrument


def save_daily_prices(db: Session, prices: List[DailyPrice]):
    """
    Saves a list of new daily prices to the database.
    """
    for price in prices:
        db.add(price)
    db.commit()


def get_instrument_by_ticker(db: Session, ticker: str) -> Instrument | None:
    """
    Fetches an instrument by its ticker.
    """
    statement = select(Instrument).where(Instrument.ticker == ticker.upper())
    return db.exec(statement).first()


def get_latest_date_for_instrument(db: Session, instrument_id: int) -> date | None:
    """
    Returns the most recent date of the data stored for
    the specified instrument.
    """
    statement = select(DailyPrice.date).where(DailyPrice.instrument_id == instrument_id).order_by(DailyPrice.date.desc())

    result = db.exec(statement).first()
    return result


def get_prices_for_instrument(
    db: Session,
    instrument_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
) -> List[DailyPrice]:
    """
    Fetches all daily prices for a given instrument,
    optionally filtered by date.
    """
    statement = select(DailyPrice).where(DailyPrice.instrument_id == instrument_id)
    if start_date:
        statement = statement.where(DailyPrice.date >= start_date)
    if end_date:
        statement = statement.where(DailyPrice.date <= end_date)

    statement = statement.order_by(DailyPrice.date)
    return db.exec(statement).all()
