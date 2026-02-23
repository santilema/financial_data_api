import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path

import httpx

from lib.env import env

logger = logging.getLogger(__name__)


class EdgarClientError(Exception):
    pass


# --- Rate limiter (module-level) ---
_rate_lock = threading.Lock()
_last_request_time: float = 0.0


def _throttle() -> None:
    global _last_request_time
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_request_time
        if elapsed < 0.1:
            time.sleep(0.1 - elapsed)
        _last_request_time = time.monotonic()


# --- CIK cache ---
_cik_cache: dict[str, str] = {}
_cache_loaded_at: float = 0.0
_CACHE_MAX_AGE_SECONDS = 7 * 24 * 3600  # 7 days


def _get_cache_path() -> Path:
    return Path(env.EDGAR_CACHE_DIR) / "company_tickers.json"


def _headers() -> dict[str, str]:
    return {"User-Agent": env.EDGAR_USER_AGENT}


def _load_cik_cache_sync() -> dict[str, str]:
    global _cik_cache, _cache_loaded_at

    if _cik_cache:
        return _cik_cache

    cache_path = _get_cache_path()
    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < _CACHE_MAX_AGE_SECONDS:
            logger.info("Loading CIK cache from %s", cache_path)
            with open(cache_path) as f:
                raw = json.load(f)
            _cik_cache = {
                str(entry["ticker"]).upper(): str(entry["cik_str"]).zfill(10)
                for entry in raw.values()
            }
            _cache_loaded_at = time.time()
            return _cik_cache

    logger.info("Fetching CIK data from SEC EDGAR")
    _throttle()
    resp = httpx.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    raw = resp.json()

    _cik_cache = {
        str(entry["ticker"]).upper(): str(entry["cik_str"]).zfill(10)
        for entry in raw.values()
    }
    _cache_loaded_at = time.time()

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(raw, f)
    logger.info("CIK cache saved to %s (%d entries)", cache_path, len(_cik_cache))

    return _cik_cache


def _resolve_cik_sync(ticker: str) -> str:
    cache = _load_cik_cache_sync()
    cik = cache.get(ticker.upper())
    if cik is None:
        raise EdgarClientError(f"Unknown ticker: {ticker}")
    return cik


def _fetch_company_facts_sync(cik: str) -> dict:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    _throttle()
    resp = httpx.get(url, headers=_headers(), timeout=30)
    if resp.status_code == 404:
        raise EdgarClientError(
            f"No company facts found for CIK {cik}. "
            "The company may not have XBRL filings."
        )
    resp.raise_for_status()
    return resp.json()


# --- Async wrappers (public API) ---
async def resolve_cik(ticker: str) -> str:
    return await asyncio.to_thread(_resolve_cik_sync, ticker)


async def fetch_company_facts(cik: str) -> dict:
    return await asyncio.to_thread(_fetch_company_facts_sync, cik)


async def load_cik_cache() -> dict[str, str]:
    return await asyncio.to_thread(_load_cik_cache_sync)
