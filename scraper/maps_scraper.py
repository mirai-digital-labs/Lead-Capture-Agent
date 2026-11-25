"""Google Maps lead scraper (Playwright browser mode + optional Serper API mode)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, asdict
from typing import Optional
from urllib.parse import quote_plus

import httpx
from playwright.async_api import async_playwright, Page

COUNTRY_CODES = {
    "united states": "us",
    "usa": "us",
    "united kingdom": "uk",
    "uk": "uk",
    "canada": "ca",
    "australia": "au",
    "united arab emirates": "ae",
    "uae": "ae",
    "saudi arabia": "sa",
    "pakistan": "pk",
    "india": "in",
    "germany": "de",
    "france": "fr",
    "turkey": "tr",
    "singapore": "sg",
    "malaysia": "my",
}


@dataclass
class Lead:
    """Represents a single business lead."""

    name: str = ""
    category: str = ""
    rating: str = ""
    reviews: str = ""
    phone: str = ""
    website: str = ""
    emails: str = ""
    email_status: str = ""
    address: str = ""
    facebook: str = ""
    instagram: str = ""
    linkedin: str = ""
    maps_url: str = ""
    lead_score: str = ""
    priority: str = ""
    ai_summary: str = ""
    cold_email: str = ""
    whatsapp_pitch: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _clean_text(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _normalize_url_key(url: str) -> str:
    """Strip query/fragment noise from Google Maps place URLs for reliable deduplication."""
    return url.split("?")[0].strip().lower()


def build_search_query(query: str, city: str = "", country: str = "") -> str:
    """Combine business keyword with optional city and country for targeted Maps search."""
    parts = [p.strip() for p in (city, country) if p and p.strip()]
    if not parts:
        return query.strip()
    location = ", ".join(parts)
    return f"{query.strip()} in {location}"


async def scrape_google_maps_serper(
    query: str,
    limit: int = 20,
    api_key: str = "",
    country: str = "",
    exclude_urls: set[str] | None = None,
) -> list[Lead]:
    """Fetch Google Maps places via Serper.dev API when an API key is provided."""
    leads: list[Lead] = []
    excluded = exclude_urls or set()
    page_num = 1
    gl = COUNTRY_CODES.get(country.strip().lower(), "")

    async with httpx.AsyncClient(timeout=20.0) as client:
        while len(leads) < limit and page_num <= 10:
            payload: dict[str, object] = {"q": query, "page": page_num}
            if gl:
                payload["gl"] = gl
            resp = await client.post(
                "https://google.serper.dev/maps",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            places = resp.json().get("places", [])
            if not places:
                break

            for place in places:
                maps_url = (
                    f"https://www.google.com/maps/place/?q=place_id:{place.get('placeId', '')}"
                    if place.get("placeId")
                    else ""
                )
                if maps_url and _normalize_url_key(maps_url) in excluded:
                    continue
                leads.append(
                    Lead(
                        name=_clean_text(place.get("title")),
                        category=_clean_text(place.get("type")),
                        rating=str(place.get("rating", "")),
                        reviews=str(place.get("ratingCount", "")),
                        phone=_clean_text(place.get("phoneNumber")),
                        website=_clean_text(place.get("website")),
                        address=_clean_text(place.get("address")),
                        maps_url=maps_url,
                    )
                )
                if len(leads) >= limit:
                    break

            page_num += 1

    return leads


async def _handle_consent_if_present(page: Page) -> None:
    """Dismiss Google consent banner if it appears."""
    for label in ("Accept all", "Reject all", "I agree"):
        btn = page.get_by_role("button", name=label)
        if await btn.count() > 0:
            try:
                await btn.first.click(timeout=2000)
                await page.wait_for_timeout(1000)
                return
            except Exception:
                pass


async def _extract_place_details(page: Page, url: str) -> Lead:
    """Open a single Google Maps place page and extract structured business fields."""
    lead = Lead(maps_url=url)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(1200)

        h1 = page.locator("h1.DUwDvf")
        if await h1.count() > 0:
            lead.name = _clean_text(await h1.first.inner_text())

        cat_btn = page.locator("button.DkEaL")
        if await cat_btn.count() > 0:
            lead.category = _clean_text(await cat_btn.first.inner_text())

        rating_el = page.locator("div.F7nice span[aria-hidden='true']")
        if await rating_el.count() > 0:
            lead.rating = _clean_text(await rating_el.first.inner_text())

        reviews_el = page.locator("div.F7nice span[aria-label*='review']")
        if await reviews_el.count() > 0:
            raw_reviews = await reviews_el.first.get_attribute("aria-label") or ""
            match = re.search(r"([\d,]+)", raw_reviews)
            if match:
                lead.reviews = match.group(1)

        addr_btn = page.locator("button[data-item-id='address']")
        if await addr_btn.count() > 0:
            lead.address = _clean_text(await addr_btn.first.inner_text())

        phone_btn = page.locator("button[data-item-id^='phone:tel:']")
        if await phone_btn.count() > 0:
            lead.phone = _clean_text(await phone_btn.first.inner_text())

        web_link = page.locator("a[data-item-id='authority']")
        if await web_link.count() > 0:
            href = await web_link.first.get_attribute("href")
            lead.website = _clean_text(href)
    except Exception:
        pass

    return lead


async def scrape_google_maps_browser(
    query: str,
    limit: int = 20,
    headless: bool = True,
    exclude_urls: set[str] | None = None,
    on_progress=None,
) -> list[Lead]:
    """Scrape Google Maps directly using Playwright Chromium (free, no API key required)."""
    search_url = f"https://www.google.com/maps/search/{quote_plus(query)}?hl=en"
    place_urls: list[str] = []
    excluded = {_normalize_url_key(u) for u in (exclude_urls or set()) if u}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-default-apps",
                "--mute-audio",
            ],
        )
        context = await browser.new_context(
            locale="en-US",
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await _handle_consent_if_present(page)

        feed = page.locator("div[role='feed']")
        try:
            await feed.wait_for(state="visible", timeout=15000)
        except Exception:
            if "/maps/place/" in page.url and _normalize_url_key(page.url) not in excluded:
                lead = await _extract_place_details(page, page.url)
                await browser.close()
                return [lead] if lead.name else []
            await browser.close()
            return []

        seen: set[str] = set()
        stagnant_rounds = 0
        while len(place_urls) < limit and stagnant_rounds < 6:
            prev_seen_count = len(seen)
            anchors = page.locator("div[role='feed'] a.hfpxzc")
            count = await anchors.count()
            for idx in range(count):
                href = await anchors.nth(idx).get_attribute("href")
                if not href or "/maps/place/" not in href:
                    continue
                norm = _normalize_url_key(href)
                if norm in seen:
                    continue
                seen.add(norm)
                if norm in excluded:
                    continue
                place_urls.append(href)
                if len(place_urls) >= limit:
                    break

            if len(place_urls) >= limit:
                break

            end_banner = page.locator("text=You've reached the end of the list.")
            if await end_banner.count() > 0:
                break

            await feed.evaluate("el => el.scrollBy(0, 1800)")
            await page.wait_for_timeout(1400)

            if len(seen) == prev_seen_count:
                stagnant_rounds += 1
            else:
                stagnant_rounds = 0

        leads: list[Lead] = []
        for idx, url in enumerate(place_urls[:limit], start=1):
            lead = await _extract_place_details(page, url)
            if lead.name:
                leads.append(lead)
            if on_progress:
                on_progress(idx, len(place_urls[:limit]), lead.name or "Unknown")

        await browser.close()
        return leads


async def fetch_maps_leads(
    query: str,
    limit: int = 20,
    headless: bool = True,
    country: str = "",
    city: str = "",
    exclude_urls: set[str] | None = None,
    on_progress=None,
) -> list[Lead]:
    """Use Serper API if SERPER_API_KEY is configured, otherwise free Playwright scraper."""
    full_query = build_search_query(query=query, city=city, country=country)
    api_key = os.getenv("SERPER_API_KEY", "").strip()
    if api_key:
        return await scrape_google_maps_serper(
            query=full_query,
            limit=limit,
            api_key=api_key,
            country=country,
            exclude_urls=exclude_urls,
        )
    return await scrape_google_maps_browser(
        query=full_query,
        limit=limit,
        headless=headless,
        exclude_urls=exclude_urls,
        on_progress=on_progress,
    )
