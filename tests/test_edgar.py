import pytest
from unittest.mock import patch, MagicMock

import services.edgar_client as edgar_mod
from services.edgar_client import (
    EdgarClientError,
    _resolve_cik_sync,
    _fetch_company_facts_sync,
    _throttle,
)


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset module-level cache before each test."""
    edgar_mod._cik_cache = {}
    edgar_mod._cache_loaded_at = 0.0
    yield
    edgar_mod._cik_cache = {}
    edgar_mod._cache_loaded_at = 0.0


MOCK_TICKERS_RESPONSE = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corporation"},
}


@patch("services.edgar_client.httpx.get")
def test_resolve_cik_success(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = MOCK_TICKERS_RESPONSE
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    cik = _resolve_cik_sync("AAPL")
    assert cik == "0000320193"


@patch("services.edgar_client.httpx.get")
def test_resolve_cik_unknown_ticker(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = MOCK_TICKERS_RESPONSE
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    with pytest.raises(EdgarClientError, match="Unknown ticker"):
        _resolve_cik_sync("ZZZZ")


@patch("services.edgar_client.httpx.get")
def test_resolve_cik_case_insensitive(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = MOCK_TICKERS_RESPONSE
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    cik = _resolve_cik_sync("aapl")
    assert cik == "0000320193"


@patch("services.edgar_client.httpx.get")
def test_fetch_company_facts_success(mock_get):
    expected = {"cik": 320193, "facts": {"us-gaap": {}}}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = expected
    mock_resp.raise_for_status = MagicMock()
    mock_get.return_value = mock_resp

    result = _fetch_company_facts_sync("0000320193")
    assert result == expected


@patch("services.edgar_client.httpx.get")
def test_fetch_company_facts_404(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_get.return_value = mock_resp

    with pytest.raises(EdgarClientError, match="No company facts found"):
        _fetch_company_facts_sync("0000000000")


def test_throttle_does_not_crash():
    """Smoke test: calling throttle twice in quick succession should not error."""
    _throttle()
    _throttle()
