"""FastAPI Web Server for Lead Capture Agent."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from exporter import deduplicate_leads, export_leads
from scraper.email_extractor import enrich_leads
from scraper.maps_scraper import fetch_maps_leads

load_dotenv()

app = FastAPI(title="Lead Capture Agent", version="1.0.0")

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
WEB_INDEX = BASE_DIR / "web" / "index.html"


class ScrapeRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=150)
    limit: int = Field(default=15, ge=1, le=50)
    skip_emails: bool = Field(default=False)


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    if not WEB_INDEX.exists():
        raise HTTPException(status_code=404, detail="Web UI not found")
    return HTMLResponse(WEB_INDEX.read_text(encoding="utf-8"))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/scrape")
async def api_scrape(payload: ScrapeRequest) -> dict:
    leads = await fetch_maps_leads(
        query=payload.query.strip(),
        limit=payload.limit,
        headless=True,
    )
    if not leads:
        return {
            "total": 0,
            "emails_found": 0,
            "leads": [],
            "csv_file": None,
            "xlsx_file": None,
        }

    leads = deduplicate_leads(leads)
    if not payload.skip_emails:
        await enrich_leads(leads)

    csv_path, xlsx_path = export_leads(leads, query=payload.query, output_dir=OUTPUT_DIR)
    emails_found = sum(1 for lead in leads if lead.emails)

    return {
        "total": len(leads),
        "emails_found": emails_found,
        "leads": [lead.to_dict() for lead in leads],
        "csv_file": csv_path.name,
        "xlsx_file": xlsx_path.name,
    }


@app.get("/api/download/{filename}")
async def download_file(filename: str) -> FileResponse:
    # Prevent path traversal by resolving inside OUTPUT_DIR
    safe_name = Path(filename).name
    target = (OUTPUT_DIR / safe_name).resolve()
    if target.parent != OUTPUT_DIR.resolve() or not target.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=target,
        filename=safe_name,
        media_type="application/octet-stream",
    )
