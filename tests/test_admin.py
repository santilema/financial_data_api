import pytest
from unittest.mock import patch, AsyncMock
from sqlmodel import Session
from fastapi.testclient import TestClient
from models import Company
from main import app, get_session
import admin as admin_module

ADMIN_HEADERS = {"X-Admin-Key": "dev"}


@pytest.fixture
def admin_client(engine, monkeypatch):
    monkeypatch.setattr(admin_module, "db_engine", engine)
    monkeypatch.setattr(admin_module, "_jobs", {})

    def get_session_override():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = get_session_override

    with patch("main.engine", engine):
        with TestClient(app) as client:
            yield client

    app.dependency_overrides.clear()


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        yield session


def test_batch_missing_auth_header(admin_client):
    response = admin_client.post("/admin/ingest/batch?tickers=AAPL")
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_params"


def test_batch_wrong_key(admin_client):
    response = admin_client.post(
        "/admin/ingest/batch?tickers=AAPL",
        headers={"X-Admin-Key": "wrong"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_batch_empty_db_no_tickers(admin_client):
    response = admin_client.post("/admin/ingest/batch", headers=ADMIN_HEADERS)
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_params"


def test_batch_specific_tickers(admin_client):
    mock = AsyncMock(return_value={"inserted": 5, "updated": 0})
    with patch("admin.ingest_company_financials", mock):
        response = admin_client.post(
            "/admin/ingest/batch?tickers=AAPL,MSFT",
            headers=ADMIN_HEADERS,
        )
    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
    assert data["total"] == 2
    assert data["status"] == "pending"

    job_id = data["job_id"]
    status_resp = admin_client.get(
        f"/admin/ingest/status/{job_id}", headers=ADMIN_HEADERS
    )
    assert status_resp.status_code == 200
    status = status_resp.json()
    assert status["status"] == "completed"
    assert status["succeeded"] == 2


def test_batch_all_companies(admin_client, db):
    db.add(Company(ticker="AAPL", name="Apple Inc."))
    db.add(Company(ticker="MSFT", name="Microsoft Corp."))
    db.commit()

    mock = AsyncMock(return_value={"inserted": 3, "updated": 0})
    with patch("admin.ingest_company_financials", mock):
        response = admin_client.post("/admin/ingest/batch", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json()["total"] == 2

    job_id = response.json()["job_id"]
    status = admin_client.get(
        f"/admin/ingest/status/{job_id}", headers=ADMIN_HEADERS
    ).json()
    assert status["succeeded"] == 2


def test_batch_one_fails(admin_client):
    from services.edgar_client import EdgarClientError

    async def side_effect(db, ticker):
        if ticker == "AAPL":
            return {"inserted": 5, "updated": 0}
        raise EdgarClientError("EDGAR down")

    with patch("admin.ingest_company_financials", side_effect=side_effect):
        response = admin_client.post(
            "/admin/ingest/batch?tickers=AAPL,FAIL",
            headers=ADMIN_HEADERS,
        )
    job_id = response.json()["job_id"]
    status = admin_client.get(
        f"/admin/ingest/status/{job_id}", headers=ADMIN_HEADERS
    ).json()
    assert status["succeeded"] == 1
    assert status["failed"] == 1


def test_batch_status_not_found(admin_client):
    response = admin_client.get(
        "/admin/ingest/status/00000000-0000-0000-0000-000000000000",
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_batch_status_completed(admin_client):
    mock = AsyncMock(return_value={"inserted": 2, "updated": 1})
    with patch("admin.ingest_company_financials", mock):
        post_resp = admin_client.post(
            "/admin/ingest/batch?tickers=AAPL",
            headers=ADMIN_HEADERS,
        )
    job_id = post_resp.json()["job_id"]
    status = admin_client.get(
        f"/admin/ingest/status/{job_id}", headers=ADMIN_HEADERS
    ).json()
    assert status["status"] == "completed"
    assert status["total"] == 1
    assert status["succeeded"] == 1
    assert status["failed"] == 0
    assert len(status["results"]) == 1
    result = status["results"][0]
    assert result["ticker"] == "AAPL"
    assert result["status"] == "ok"
    assert result["inserted"] == 2


def test_batch_deduplication(admin_client):
    mock = AsyncMock(return_value={"inserted": 5, "updated": 0})
    with patch("admin.ingest_company_financials", mock):
        response = admin_client.post(
            "/admin/ingest/batch?tickers=AAPL,AAPL,AAPL",
            headers=ADMIN_HEADERS,
        )
    assert response.json()["total"] == 1

    job_id = response.json()["job_id"]
    status = admin_client.get(
        f"/admin/ingest/status/{job_id}", headers=ADMIN_HEADERS
    ).json()
    assert status["succeeded"] == 1
