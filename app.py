"""FastAPI Web Server for Lead Capture Agent with user accounts, credits, and async jobs."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from db import (
    add_user_credits,
    authenticate_user,
    clear_saved_leads,
    create_user,
    deduct_credits,
    get_saved_leads,
    get_seen_urls,
    get_user_by_token,
    init_db,
    save_search_and_leads,
)
from exporter import deduplicate_leads, export_leads
from scraper.ai_assistant import analyze_leads_with_ai
from scraper.email_extractor import enrich_leads
from scraper.maps_scraper import build_search_query, fetch_maps_leads

load_dotenv()

app = FastAPI(title="Lead Capture Agent", version="2.0.0")
init_db()

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
WEB_INDEX = BASE_DIR / "web" / "index.html"

JOBS: dict[str, dict[str, Any]] = {}


class AuthRequest(BaseModel):
    email: str = Field(..., min_length=4, max_length=120)
    password: str = Field(..., min_length=6, max_length=120)


class AdminCreditRequest(BaseModel):
    email: str = Field(..., min_length=4, max_length=120)
    add_credits: int = Field(..., ge=1, le=100000)


class ScrapeRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=150)
    country: str = Field(default="", max_length=80)
    city: str = Field(default="", max_length=150)
    limit: int = Field(default=15, ge=1, le=50)
    skip_emails: bool = Field(default=False)
    offer: str = Field(default="", max_length=300)
    gemini_api_key: str = Field(default="", max_length=200)
    exclude_urls: list[str] = Field(default_factory=list)


class WebhookRequest(BaseModel):
    webhook_url: str = Field(..., min_length=8, max_length=500)
    query: str = Field(default="")
    leads: list[dict[str, Any]] = Field(default_factory=list)


def get_current_user(authorization: str = Header(default="")) -> dict[str, Any]:
    token = authorization.replace("Bearer ", "").strip() if authorization.startswith("Bearer ") else ""
    user = get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in to continue")
    return user


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    if not WEB_INDEX.exists():
        raise HTTPException(status_code=404, detail="Web UI not found")
    return HTMLResponse(WEB_INDEX.read_text(encoding="utf-8"))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/register")
async def register(payload: AuthRequest) -> dict[str, Any]:
    try:
        token, user = create_user(payload.email, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"token": token, "user": user}


@app.post("/api/auth/login")
async def login(payload: AuthRequest) -> dict[str, Any]:
    result = authenticate_user(payload.email, payload.password)
    if not result:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token, user = result
    return {"token": token, "user": user}


@app.get("/api/auth/me")
async def me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"user": user}


@app.post("/api/admin/credits")
async def grant_credits(
    payload: AdminCreditRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    new_balance = add_user_credits(payload.email, payload.add_credits)
    if new_balance is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {"email": payload.email, "credits": new_balance}


async def _run_scrape_job(job_id: str, user_id: int, payload: ScrapeRequest) -> None:
    job = JOBS[job_id]
    try:
        job["status"] = "running"
        cities = [c.strip() for c in payload.city.split(",") if c.strip()] or [""]
        cities = cities[:5]

        all_leads = []
        seen_urls = {u.split("?")[0].strip().lower() for u in payload.exclude_urls if u}
        if payload.exclude_urls:
            seen_urls.update(get_seen_urls(user_id))

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
                "credits": None,
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
        save_search_and_leads(user_id, payload.query, payload.country, payload.city, leads)
        remaining_credits = deduct_credits(user_id, len(leads))
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
            "credits": remaining_credits,
        }
    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc) or "Scraping failed"


@app.post("/api/scrape")
async def start_scrape(
    payload: ScrapeRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, str]:
    if user["credits"] <= 0:
        raise HTTPException(status_code=402, detail="Out of lead credits. Contact admin to top up.")

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "user_id": user["id"],
        "status": "queued",
        "message": "Starting scraper...",
        "result": None,
        "error": None,
    }
    asyncio.create_task(_run_scrape_job(job_id, user["id"], payload))
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(
    job_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/webhook")
async def send_to_webhook(
    payload: WebhookRequest,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
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
                    "user": user["email"],
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


@app.get("/api/leads")
async def list_saved_leads(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    rows = get_saved_leads(user["id"], limit=500)
    return {"total": len(rows), "leads": rows}


@app.delete("/api/leads")
async def delete_saved_leads(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    clear_saved_leads(user["id"])
    return {"status": "cleared"}


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
