import yfinance as yf
import pandas as pd
import asyncio
from typing import List, Tuple
from datetime import timedelta, date

from models import Instrument, DailyPrice


class YahooFinanceError(Exception):
    pass


def _fetch_data_sync(ticker: str) -> Tuple[Instrument, List[DailyPrice]]:
    """
    Synchronous (blocking) function that encapsulates network request.
    """
    try:
        ticker_obj = yf.Ticker(ticker)
        info = ticker_obj.info
    except Exception as e:
        raise YahooFinanceError(f"Failed to get ticker data for {ticker}: {e}")

    # `auto_adjust=True` overwrites OHLC with adjusted values
    hist_df = ticker_obj.history(period="max", auto_adjust=True)

    if hist_df.empty:
        raise YahooFinanceError(f"No history data found for {ticker}")

    # create models
    name = info.get("longName", ticker)
    instrument = Instrument(ticker=ticker.upper(), name=name)
    prices_list: List[DailyPrice] = []
    hist_df = hist_df.reset_index()

    for row in hist_df.itertuples():
        price_data = DailyPrice(
            instrument_id=0,  # placeholder
            date=row.Date.date(),
            open=row.Open,
            high=row.High,
            low=row.Low,
            close=row.Close,
            volume=row.Volume,
        )
        prices_list.append(price_data)

    return instrument, prices_list


async def fetch_daily_data(ticker: str) -> Tuple[Instrument, List[DailyPrice]]:
    """
    Public, asynchronous wrapper that can be called from FastAPI endpoints.
    """
    try:
        # run blocking function in separate thread
        instrument, prices = await asyncio.to_thread(_fetch_data_sync, ticker)
        return instrument, prices
    except Exception as e:
        if isinstance(e, YahooFinanceError):
            raise e
        raise YahooFinanceError(f"An unexpected error ocurred for {ticker}: {e}")


def _fetch_data_since_sync(ticker: str, start_date: date) -> List[DailyPrice]:
    """
    Private, synchronous (blocking) function that fetches data
    since a given start date.
    """
    start_date_str = start_date.strftime("%Y-%m-%d")

    try:
        ticker_obj = yf.Ticker(ticker)
        hist_df = ticker_obj.history(start=start_date_str, auto_adjust=True)
    except Exception as e:
        raise YahooFinanceError(f"Failed to fetch data for {ticker}: {e}")

    if hist_df.empty:
        return []

    prices_list: List[DailyPrice] = []
    hist_df = hist_df.reset_index()

    for row in hist_df.itertuples():
        price_data = DailyPrice(
            instrument_id=0,  # placeholder
            date=row.Date.date(),
            open=row.Open,
            low=row.Low,
            high=row.High,
            close=row.Close,
            volume=row.Volume,
        )
        prices_list.append(price_data)

    return prices_list


async def fetch_daily_data_since(ticker: str, start_date: date) -> List[DailyPrice]:
    """
    Public, asynchronous wrapper that can be called from FastAPI endpoints.
    """
    try:
        prices = await asyncio.to_thread(_fetch_data_since_sync, ticker, start_date)
        return prices
    except Exception as e:
        if isinstance(e, YahooFinanceError):
            raise e
        raise YahooFinanceError(f"An unexpected error occurred for {ticker}: {e}")
