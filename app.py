"""FastAPI Web Server for Lead Capture Agent with async background jobs."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
import httpx
from pydantic import BaseModel, Field

from exporter import deduplicate_leads, export_leads
from scraper.ai_assistant import analyze_leads_with_ai
from scraper.email_extractor import enrich_leads
from scraper.maps_scraper import build_search_query, fetch_maps_leads

load_dotenv()

app = FastAPI(title="Lead Capture Agent", version="1.2.0")

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
WEB_INDEX = BASE_DIR / "web" / "index.html"

JOBS: dict[str, dict[str, Any]] = {}


class ScrapeRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=150)
    country: str = Field(default="", max_length=80)
    city: str = Field(default="", max_length=150)
    limit: int = Field(default=15, ge=1, le=50)
    skip_emails: bool = Field(default=False)
    offer: str = Field(default="", max_length=300)
    gemini_api_key: str = Field(default="", max_length=200)
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
        cities = [c.strip() for c in payload.city.split(",") if c.strip()] or [""]
        cities = cities[:5]  # Cap to 5 cities per job to protect container memory

        all_leads = []
        seen_urls = {u.split("?")[0].strip().lower() for u in payload.exclude_urls if u}

        for city_idx, city_name in enumerate(cities, start=1):
            full_query = build_search_query(payload.query, city_name, payload.country)
            prefix = f"[City {city_idx}/{len(cities)}: {city_name}] " if city_name else ""
            job["message"] = f"{prefix}Searching Google Maps for '{full_query}'..."

            def on_maps_progress(idx: int, total: int, name: str, _prefix: str = prefix) -> None:
                job["message"] = f"{_prefix}Maps ({idx}/{total}): {name}"

            city_leads = await fetch_maps_leads(
                query=payload.query.strip(),
                limit=payload.limit,
                headless=True,
                country=payload.country.strip(),
                city=city_name,
                exclude_urls=seen_urls,
                on_progress=on_maps_progress,
            )
            for lead in city_leads:
                if lead.maps_url:
                    seen_urls.add(lead.maps_url.split("?")[0].strip().lower())
            all_leads.extend(city_leads)

        if not all_leads:
            job["status"] = "done"
            job["result"] = {
                "total": 0,
                "emails_found": 0,
                "leads": [],
                "csv_file": None,
                "xlsx_file": None,
            }
            return

        leads = deduplicate_leads(all_leads)

        if not payload.skip_emails:
            done_count = 0

            def on_email_progress(name: str) -> None:
                nonlocal done_count
                done_count += 1
                job["message"] = f"Scanning websites ({done_count}/{len(leads)}): {name}"

            await enrich_leads(leads, on_progress=on_email_progress)

        ai_count = 0

        def on_ai_progress(name: str) -> None:
            nonlocal ai_count
            ai_count += 1
            job["message"] = f"AI Assistant analyzing ({ai_count}/{len(leads)}): {name}"

        await analyze_leads_with_ai(
            leads,
            offer=payload.offer,
            api_key=payload.gemini_api_key,
            on_progress=on_ai_progress,
        )

        export_label = build_search_query(payload.query, "_".join(cities) if any(cities) else "", payload.country)
        csv_path, xlsx_path = export_leads(leads, query=export_label, output_dir=OUTPUT_DIR)
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


class WebhookRequest(BaseModel):
    webhook_url: str = Field(..., min_length=8, max_length=500)
    query: str = Field(default="")
    leads: list[dict[str, Any]] = Field(default_factory=list)


@app.post("/api/webhook")
async def send_to_webhook(payload: WebhookRequest) -> dict[str, Any]:
    url = payload.webhook_url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Webhook URL must start with http:// or https://")
    if not payload.leads:
        raise HTTPException(status_code=400, detail="No leads to send")

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.post(
                url,
                json={
                    "source": "Lead Capture Agent",
                    "query": payload.query,
                    "count": len(payload.leads),
                    "leads": payload.leads,
                },
            )
            if resp.status_code >= 400:
                raise HTTPException(status_code=502, detail=f"Webhook returned HTTP {resp.status_code}")
            return {"status": "sent", "sent_count": len(payload.leads), "remote_status": resp.status_code}
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Webhook delivery failed: {exc}") from exc


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
