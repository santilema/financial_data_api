import os
import uuid
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query
from sqlmodel import Session
from database import engine as db_engine, get_session
import repository
from schemas import ApiException
from services.edgar_pipeline import ingest_company_financials

router = APIRouter(prefix="/admin", tags=["Admin"])
logger = logging.getLogger(__name__)
_jobs: dict[str, dict] = {}


def _require_admin_key(x_admin_key: str = Header(...)):
    expected = os.getenv("ADMIN_KEY", "dev")
    if x_admin_key != expected:
        raise ApiException(403, "forbidden", "Invalid admin key.")


@router.post("/ingest/batch")
async def batch_ingest(
    background_tasks: BackgroundTasks,
    tickers: str | None = Query(
        None,
        description="Comma-separated tickers to ingest. Omit to ingest all registered companies.",
    ),
    db: Session = Depends(get_session),
    _: None = Depends(_require_admin_key),
):
    """
    Enqueue a background EDGAR ingestion job for one or more companies.
    Requires `X-Admin-Key` header. Returns a `job_id` for status polling.
    Use `GET /admin/ingest/status/{job_id}` to check progress.
    """
    if tickers:
        ticker_list = list(
            dict.fromkeys(t.strip().upper() for t in tickers.split(",") if t.strip())
        )
    else:
        companies = repository.get_all_companies(db)
        ticker_list = [c.ticker for c in companies]

    if not ticker_list:
        raise ApiException(
            422, "invalid_params", "No tickers to ingest.", field="tickers"
        )

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "status": "pending",
        "total": len(ticker_list),
        "succeeded": 0,
        "failed": 0,
        "results": [],
    }

    background_tasks.add_task(_run_batch, job_id, ticker_list)
    return {"job_id": job_id, "total": len(ticker_list), "status": "pending"}


@router.get("/ingest/status/{job_id}")
async def batch_status(job_id: str, _: None = Depends(_require_admin_key)):
    """
    Poll the status of a batch ingestion job. Requires `X-Admin-Key` header.
    Returns status (pending/running/completed), per-ticker results, and counts.
    """
    job = _jobs.get(job_id)
    if not job:
        raise ApiException(404, "not_found", f"Job {job_id} not found.")
    return job


async def _run_batch(job_id: str, ticker_list: list[str]):
    _jobs[job_id]["status"] = "running"
    with Session(db_engine) as db:
        for i, ticker in enumerate(ticker_list):
            logger.info("Batch ingesting %d/%d: %s", i + 1, len(ticker_list), ticker)
            try:
                result = await ingest_company_financials(db, ticker)
                _jobs[job_id]["succeeded"] += 1
                _jobs[job_id]["results"].append(
                    {
                        "ticker": ticker,
                        "status": "ok",
                        "inserted": result["inserted"],
                        "updated": result["updated"],
                    }
                )
            except Exception as e:
                _jobs[job_id]["failed"] += 1
                _jobs[job_id]["results"].append(
                    {"ticker": ticker, "status": "error", "detail": str(e)}
                )
    _jobs[job_id]["status"] = "completed"
