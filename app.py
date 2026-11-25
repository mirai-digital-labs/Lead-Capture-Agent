"""FastAPI Web Server for Lead Capture Agent with async background jobs."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from exporter import deduplicate_leads, export_leads
from scraper.email_extractor import enrich_leads
from scraper.maps_scraper import fetch_maps_leads

load_dotenv()

app = FastAPI(title="Lead Capture Agent", version="1.1.0")

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
WEB_INDEX = BASE_DIR / "web" / "index.html"

# In-memory job store to avoid 503 proxy timeouts on long scrapes
JOBS: dict[str, dict[str, Any]] = {}


class ScrapeRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=150)
    limit: int = Field(default=15, ge=1, le=50)
    skip_emails: bool = Field(default=False)
    exclude_urls: list[str] = Field(default_factory=list)


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    if not WEB_INDEX.exists():
        raise HTTPException(status_code=404, detail="Web UI not found")
    return HTMLResponse(WEB_INDEX.read_text(encoding="utf-8"))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def _run_scrape_job(job_id: str, payload: ScrapeRequest) -> None:
    job = JOBS[job_id]
    try:
        job["status"] = "running"
        job["message"] = "Searching Google Maps..."

        def on_maps_progress(idx: int, total: int, name: str) -> None:
            job["message"] = f"Maps ({idx}/{total}): {name}"

        leads = await fetch_maps_leads(
            query=payload.query.strip(),
            limit=payload.limit,
            headless=True,
            exclude_urls=set(payload.exclude_urls),
            on_progress=on_maps_progress,
        )

        if not leads:
            job["status"] = "done"
            job["result"] = {
                "total": 0,
                "emails_found": 0,
                "leads": [],
                "csv_file": None,
                "xlsx_file": None,
            }
            return

        leads = deduplicate_leads(leads)

        if not payload.skip_emails:
            done_count = 0

            def on_email_progress(name: str) -> None:
                nonlocal done_count
                done_count += 1
                job["message"] = f"Scanning websites ({done_count}/{len(leads)}): {name}"

            await enrich_leads(leads, on_progress=on_email_progress)

        csv_path, xlsx_path = export_leads(leads, query=payload.query, output_dir=OUTPUT_DIR)
        emails_found = sum(1 for lead in leads if lead.emails)

        job["status"] = "done"
        job["message"] = "Completed"
        job["result"] = {
            "total": len(leads),
            "emails_found": emails_found,
            "leads": [lead.to_dict() for lead in leads],
            "csv_file": csv_path.name,
            "xlsx_file": xlsx_path.name,
        }
    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc) or "Scraping failed"


@app.post("/api/scrape")
async def start_scrape(payload: ScrapeRequest) -> dict[str, str]:
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "status": "queued",
        "message": "Starting scraper...",
        "result": None,
        "error": None,
    }
    asyncio.create_task(_run_scrape_job(job_id, payload))
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/download/{filename}")
async def download_file(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    target = (OUTPUT_DIR / safe_name).resolve()
    if target.parent != OUTPUT_DIR.resolve() or not target.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=target,
        filename=safe_name,
        media_type="application/octet-stream",
    )
