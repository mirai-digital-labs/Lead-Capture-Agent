"""Website email and social media extractor for enriching Google Maps leads."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from scraper.maps_scraper import Lead

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

IGNORED_EMAIL_DOMAINS = {
    "example.com",
    "domain.com",
    "email.com",
    "sentry.io",
    "wixpress.com",
    "w3.org",
    "schema.org",
    "googleapis.com",
    "googletagmanager.com",
    "wordpress.org",
}

IGNORED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", ".woff")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def _is_valid_email(email: str) -> bool:
    lower = email.lower().strip()
    if any(lower.endswith(ext) for ext in IGNORED_EXTENSIONS):
        return False
    domain = lower.split("@")[-1]
    if domain in IGNORED_EMAIL_DOMAINS:
        return False
    if len(lower) > 80:
        return False
    return True


def _extract_from_html(html: str, base_url: str) -> tuple[set[str], dict[str, str], list[str]]:
    """Extract emails, social links, and promising contact/about subpage URLs."""
    soup = BeautifulSoup(html, "html.parser")
    emails: set[str] = set()
    socials: dict[str, str] = {}
    subpages: list[str] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        lower_href = href.lower()

        if lower_href.startswith("mailto:"):
            candidate = href[7:].split("?")[0].strip()
            if EMAIL_REGEX.fullmatch(candidate) and _is_valid_email(candidate):
                emails.add(candidate.lower())

        elif "facebook.com/" in lower_href and "facebook" not in socials:
            socials["facebook"] = href
        elif "instagram.com/" in lower_href and "instagram" not in socials:
            socials["instagram"] = href
        elif "linkedin.com/" in lower_href and "linkedin" not in socials:
            socials["linkedin"] = href

        elif any(k in lower_href for k in ("contact", "about", "reach-us", "get-in-touch")):
            full = urljoin(base_url, href)
            if urlparse(full).netloc == urlparse(base_url).netloc and full not in subpages:
                subpages.append(full)

    text = soup.get_text(" ", strip=True)
    for match in EMAIL_REGEX.findall(text):
        if _is_valid_email(match):
            emails.add(match.lower())

    return emails, socials, subpages[:2]


async def enrich_single_lead(
    client: httpx.AsyncClient,
    lead: Lead,
    semaphore: asyncio.Semaphore,
    on_progress=None,
) -> Lead:
    """Visit a lead's website (and contact page) to extract emails and social links."""
    if not lead.website:
        if on_progress:
            on_progress(lead.name)
        return lead

    url = lead.website if lead.website.startswith(("http://", "https://")) else f"https://{lead.website}"

    async with semaphore:
        all_emails: set[str] = set()
        socials: dict[str, str] = {}

        try:
            resp = await client.get(url, follow_redirects=True)
            if resp.status_code < 400 and "text/html" in resp.headers.get("content-type", ""):
                found_emails, found_socials, subpages = _extract_from_html(resp.text, str(resp.url))
                all_emails.update(found_emails)
                socials.update(found_socials)

                if not all_emails:
                    for sub_url in subpages:
                        try:
                            sub_resp = await client.get(sub_url, follow_redirects=True)
                            if sub_resp.status_code < 400 and "text/html" in sub_resp.headers.get("content-type", ""):
                                sub_emails, sub_socials, _ = _extract_from_html(sub_resp.text, str(sub_resp.url))
                                all_emails.update(sub_emails)
                                for k, v in sub_socials.items():
                                    socials.setdefault(k, v)
                                if all_emails:
                                    break
                        except Exception:
                            continue
        except Exception:
            pass

        lead.emails = ", ".join(sorted(all_emails))
        lead.facebook = socials.get("facebook", "")
        lead.instagram = socials.get("instagram", "")
        lead.linkedin = socials.get("linkedin", "")

    if on_progress:
        on_progress(lead.name)
    return lead


async def enrich_leads(
    leads: list[Lead],
    concurrency: int = 8,
    on_progress=None,
) -> list[Lead]:
    """Concurrently enrich all leads with website emails and social media links."""
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(headers=HEADERS, timeout=12.0, verify=False) as client:
        tasks = [
            enrich_single_lead(client, lead, semaphore, on_progress=on_progress)
            for lead in leads
        ]
        await asyncio.gather(*tasks)
    return leads
