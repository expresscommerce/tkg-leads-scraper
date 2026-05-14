"""FastAPI wrapper around the Lead Scraper pipeline."""

from __future__ import annotations

import os
from pathlib import Path as _Path

# Point Playwright to a stable, project-local browser cache (avoids sandbox
# tools clearing the default ~/.cache/ms-playwright location).
_PW_DIR = _Path(__file__).parent / ".playwright"
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_PW_DIR))

import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from scraper.pipeline import run_pipeline

app = FastAPI(title="Lead Scraper API", version="2.0")

# CORS for local Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_jobs: dict[str, dict[str, Any]] = {}
_stop_events: dict[str, threading.Event] = {}
_lock = threading.Lock()


class RunRequest(BaseModel):
    query: str | list[str] = Field(
        ...,
        description="Single keyword, list of keywords, or newline/semicolon-separated string.",
    )
    location: str | list[str] = Field(
        ...,
        description="Single location, list of locations, or newline/semicolon-separated string.",
    )
    max_results: int = Field(
        default=20, ge=1, le=5000,
        description="Results per (keyword × location).",
    )
    skip_websites: bool = False
    skip_meta: bool = False
    output_basename: str | None = None


def _execute(job_id: str, req: RunRequest) -> None:
    stop_event = _stop_events[job_id]

    def progress(stage: str, current: int, total: int, message: str) -> None:
        if stop_event.is_set():
            raise InterruptedError("Job stopped by user")
        with _lock:
            log = _jobs[job_id]["logs"]
            log.append(f"[{stage}] {current}/{total} {message}")
            _jobs[job_id]["stage"] = stage
            _jobs[job_id]["current"] = current
            _jobs[job_id]["total"] = total
            _jobs[job_id]["message"] = message

    with _lock:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["started_at"] = time.time()

    try:
        result = run_pipeline(
            query=req.query,
            location=req.location,
            max_results=req.max_results,
            skip_websites=req.skip_websites,
            skip_meta=req.skip_meta,
            output_basename=req.output_basename,
            progress=progress,
        )
        with _lock:
            _jobs[job_id].update(
                status="finished",
                count=len(result.businesses),
                csv_path=str(result.csv_path) if result.csv_path else None,
                ended_at=time.time(),
            )
    except InterruptedError:
        with _lock:
            _jobs[job_id].update(status="stopped", error="Job stopped by user", ended_at=time.time())
    except Exception as exc:
        with _lock:
            _jobs[job_id].update(status="failed", error=str(exc), ended_at=time.time())


@app.get("/")
def root() -> dict[str, str]:
    return {"status": "ok", "service": "Lead Scraper API"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.post("/run")
def run_scrape(req: RunRequest) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {
            "status": "queued",
            "logs": deque(maxlen=500),
            "stage": "queued",
            "current": 0,
            "total": 0,
            "message": "",
        }
        _stop_events[job_id] = threading.Event()
    threading.Thread(target=_execute, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id, "status": "queued"}


@app.get("/status/{job_id}")
def status(job_id: str) -> dict[str, Any]:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return {k: v for k, v in job.items() if k != "logs"}


@app.get("/logs/{job_id}")
def logs(job_id: str, tail: int = 100) -> dict[str, Any]:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return {"job_id": job_id, "lines": list(job["logs"])[-max(1, tail):]}


@app.post("/stop/{job_id}")
def stop_job(job_id: str) -> dict[str, Any]:
    with _lock:
        if job_id not in _jobs:
            raise HTTPException(404, "job not found")
        evt = _stop_events.get(job_id)
        if evt:
            evt.set()
    return {"job_id": job_id, "status": "stop_requested"}


@app.get("/download/{job_id}")
def download_csv(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        csv_path = job.get("csv_path")
    if not csv_path or not Path(csv_path).exists():
        raise HTTPException(404, "CSV not available")
    return FileResponse(csv_path, media_type="text/csv", filename=Path(csv_path).name)


@app.get("/jobs")
def list_jobs() -> dict[str, Any]:
    with _lock:
        return {jid: {k: v for k, v in j.items() if k != "logs"} for jid, j in _jobs.items()}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)
