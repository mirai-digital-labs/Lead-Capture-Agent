"""AI Sales Assistant — scores leads (0-100) and drafts personalized cold emails & WhatsApp pitches using Gemini."""

from __future__ import annotations

import asyncio
import json
import os
import re

import httpx

from scraper.maps_scraper import Lead

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"


def _fallback_analysis(lead: Lead, offer: str) -> None:
    """Deterministic scoring and outreach template when no Gemini API key is configured."""
    score = 35
    if lead.website:
        score += 20
    if lead.emails:
        score += 25 if lead.email_status == "Verified" else 15
    if lead.phone:
        score += 10
    try:
        if float(lead.rating or 0) >= 4.0:
            score += 10
    except ValueError:
        pass
    score = min(score, 100)

    lead.lead_score = str(score)
    lead.priority = "Hot" if score >= 75 else ("Warm" if score >= 50 else "Cold")

    offer_text = offer.strip() or "our services to help grow your business"
    lead.ai_summary = (
        f"{lead.name} ({lead.category or 'Local Business'}) — "
        f"{'Verified email & website available' if lead.email_status == 'Verified' else 'Contact info captured'}."
    )
    lead.cold_email = (
        f"Subject: Quick question for {lead.name}\n\n"
        f"Hi {lead.name} team,\n\n"
        f"I came across your business while researching {lead.category or 'companies'} in {lead.address or 'your area'}. "
        f"We help businesses like yours with {offer_text}.\n\n"
        f"Would you be open to a quick 5-minute chat this week?\n\nBest regards"
    )
    lead.whatsapp_pitch = (
        f"Hi {lead.name} team! I found your business on Google Maps. "
        f"We specialize in {offer_text} and would love to share how we can help {lead.name}. "
        f"Can I send over a quick overview?"
    )


def _extract_json_block(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


async def _analyze_single_lead_gemini(
    client: httpx.AsyncClient,
    lead: Lead,
    offer: str,
    api_key: str,
    semaphore: asyncio.Semaphore,
    on_progress=None,
) -> None:
    prompt = (
        "You are an expert B2B sales assistant. Analyze this business lead and return ONLY valid JSON with keys: "
        '"lead_score" (integer 1-100), "priority" ("Hot"|"Warm"|"Cold"), '
        '"ai_summary" (1 concise sentence), "cold_email" (short personalized email with Subject line), '
        '"whatsapp_pitch" (short friendly 2-sentence WhatsApp message).\n\n'
        f"Our Offer/Service: {offer or 'B2B growth, digital solutions & supply partnership'}\n"
        f"Business Name: {lead.name}\n"
        f"Category: {lead.category}\n"
        f"Rating: {lead.rating} ({lead.reviews} reviews)\n"
        f"Website: {lead.website or 'None'}\n"
        f"Email: {lead.emails or 'None'} ({lead.email_status or 'N/A'})\n"
        f"Phone: {lead.phone or 'None'}\n"
        f"Address: {lead.address or 'None'}\n"
    )

    async with semaphore:
        try:
            resp = await client.post(
                f"{GEMINI_ENDPOINT}?key={api_key}",
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.4, "maxOutputTokens": 450},
                },
            )
            if resp.status_code == 200:
                candidates = resp.json().get("candidates", [])
                raw_text = (
                    candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    if candidates
                    else ""
                )
                parsed = _extract_json_block(raw_text)
                if parsed:
                    score = max(1, min(100, int(parsed.get("lead_score", 60))))
                    lead.lead_score = str(score)
                    lead.priority = str(parsed.get("priority", "Warm"))
                    lead.ai_summary = str(parsed.get("ai_summary", ""))[:300]
                    lead.cold_email = str(parsed.get("cold_email", ""))[:1200]
                    lead.whatsapp_pitch = str(parsed.get("whatsapp_pitch", ""))[:600]
                    if on_progress:
                        on_progress(lead.name)
                    return
        except Exception:
            pass

    _fallback_analysis(lead, offer)
    if on_progress:
        on_progress(lead.name)


async def analyze_leads_with_ai(
    leads: list[Lead],
    offer: str = "",
    api_key: str = "",
    on_progress=None,
) -> list[Lead]:
    """Qualify leads and generate outreach copy using Gemini AI (or smart fallback)."""
    key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
    if not key:
        for lead in leads:
            _fallback_analysis(lead, offer)
            if on_progress:
                on_progress(lead.name)
        return leads

    semaphore = asyncio.Semaphore(5)
    async with httpx.AsyncClient(timeout=25.0) as client:
        tasks = [
            _analyze_single_lead_gemini(client, lead, offer, key, semaphore, on_progress=on_progress)
            for lead in leads
        ]
        await asyncio.gather(*tasks)
    return leads
