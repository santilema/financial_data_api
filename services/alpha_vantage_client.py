import httpx
from datetime import date, datetime
from typing import Dict, List, Any

from models import Instrument, DailyPrice
from lib.env import env

ALPHA_VANTAGE_API_KEY = env.ALPHA_VANTAGE_API_KEY
BASE_URL = "https://www.alphavantage.co/query"


class AlphaVantageError(Exception):
    pass  # for excep wrap


async def fetch_daily_data(ticker: str) -> (Instrument, List[DailyPrice]):
    """
    Fetches the adjusted daily time series for a specified ticker,
    and matches it to the data model.
    """
    params = {
        "function": "TIME_SERIES_DAILY_ADJUSTED",
        "symbol": ticker,
        "outputsize": "full",  # 20+ years of data
        "apiKey": ALPHA_VANTAGE_API_KEY,
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(BASE_URL, params=params)
            response.raise_for_status()  # 4xx and 5xx errors
        except httpx.HTTPStatusError as exc:
            raise AlphaVantageError(f"API request failed: {exc.response.status_code}")

    data = response.json()

    # 1. Error handling and parsing metadata
    if "Error Message" in data:
        raise AlphaVantageError(f"API Error: {data['Error Message']}")
    if "Note" in data:
        # Hit API limit (25 per day)
        raise AlphaVantageError(f"API Error: {data['Note']}")
    if "Meta Data" not in data or "Time Series (Daily)" not in data:
        raise AlphaVantageError(f"Invalid data returned for ticker: {ticker}")

    meta_data = data["Meta Data"]

    # 2. Create the Instrument
    instrument = Instrument(
        ticker=meta_data["2. Symbol"],
        name=f"Name for {ticker}",  # No name in metadata :(
        # TODO - fetch company name
    )

    # 3. Parse time series data
    time_series = data["Time Series (Daily)"]
    prices_list: List[DailyPrice] = []

    for date_str, values in time_series.items():
        try:
            price_data = DailyPrice(
                instrument_id=0,
                date=datetime.strptime(date_str, "%Y-%m-%d").date(),
                open=float(values["1. open"]),
                high=float(values["2. high"]),
                low=float(values["3. low"]),
                close=float(values["4.close"]),
                volume=int(values["6. volume"]),
            )
        except (ValueError, KeyError) as e:
            # TODO - Log this error
            print(f"Warning: Skipped bad data for {ticker} on {date_str}: {e}")
            continue

    if not prices_list:
        raise AlphaVantageError(f"Time series data not found for {ticker}")

    prices_list.sort(key=lambda p: p.date)  # oldest to newest

    return instrument, prices_list
