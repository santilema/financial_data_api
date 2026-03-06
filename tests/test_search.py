import pytest
from sqlmodel import Session
from models import Company


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        yield session


def _add_company(db, ticker, name, sector=None):
    c = Company(ticker=ticker, name=name, sector=sector)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_search_no_params(client, db):
    _add_company(db, "AAPL", "Apple Inc.", sector="Technology")
    _add_company(db, "MSFT", "Microsoft Corp.", sector="Technology")
    response = client.get("/search")
    assert response.status_code == 200
    tickers = [r["ticker"] for r in response.json()]
    assert "AAPL" in tickers
    assert "MSFT" in tickers


def test_search_by_ticker_exact(client, db):
    _add_company(db, "AAPL", "Apple Inc.", sector="Technology")
    _add_company(db, "MSFT", "Microsoft Corp.", sector="Technology")
    response = client.get("/search?q=AAPL")
    assert response.status_code == 200
    tickers = [r["ticker"] for r in response.json()]
    assert "AAPL" in tickers


def test_search_by_ticker_partial(client, db):
    _add_company(db, "AAPL", "Apple Inc.")
    _add_company(db, "MSFT", "Microsoft Corp.")
    response = client.get("/search?q=AA")
    assert response.status_code == 200
    tickers = [r["ticker"] for r in response.json()]
    assert "AAPL" in tickers
    assert "MSFT" not in tickers


def test_search_by_name(client, db):
    _add_company(db, "AAPL", "Apple Inc.")
    _add_company(db, "MSFT", "Microsoft Corp.")
    response = client.get("/search?q=apple")
    assert response.status_code == 200
    tickers = [r["ticker"] for r in response.json()]
    assert "AAPL" in tickers
    assert "MSFT" not in tickers


def test_search_exact_ticker_ranks_first(client, db):
    _add_company(db, "AAPLX", "Apple Extended Fund")
    _add_company(db, "AAPL", "Apple Inc.")
    response = client.get("/search?q=AAPL")
    assert response.status_code == 200
    results = response.json()
    assert results[0]["ticker"] == "AAPL"


def test_search_sector_filter(client, db):
    _add_company(db, "AAPL", "Apple Inc.", sector="Technology")
    _add_company(db, "JPM", "JPMorgan Chase", sector="Financials")
    response = client.get("/search?sector=Technology")
    assert response.status_code == 200
    tickers = [r["ticker"] for r in response.json()]
    assert "AAPL" in tickers
    assert "JPM" not in tickers


def test_search_limit(client, db):
    for i in range(5):
        _add_company(db, f"TK{i}", f"Company {i}")
    response = client.get("/search?limit=1")
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_search_limit_too_large(client):
    response = client.get("/search?limit=101")
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_params"


def test_search_empty_results(client, db):
    _add_company(db, "AAPL", "Apple Inc.")
    response = client.get("/search?q=ZZZNOMATCH")
    assert response.status_code == 200
    assert response.json() == []
