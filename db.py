"""Persistent lead & search history storage (SQLite by default, optional PostgreSQL via DATABASE_URL)."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scraper.maps_scraper import Lead

DATA_DIR = Path(__file__).resolve().parent / "data"
SQLITE_PATH = DATA_DIR / "leads.db"


def _get_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create leads and search_logs tables if they do not exist."""
    with _get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedup_key TEXT UNIQUE NOT NULL,
                name TEXT,
                category TEXT,
                rating TEXT,
                reviews TEXT,
                phone TEXT,
                website TEXT,
                emails TEXT,
                email_status TEXT,
                address TEXT,
                facebook TEXT,
                instagram TEXT,
                linkedin TEXT,
                maps_url TEXT,
                lead_score TEXT,
                priority TEXT,
                ai_summary TEXT,
                cold_email TEXT,
                whatsapp_pitch TEXT,
                search_query TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS search_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT,
                country TEXT,
                city TEXT,
                total_leads INTEGER,
                created_at TEXT
            )
            """
        )
        conn.commit()


def _dedup_key(lead: Lead) -> str:
    if lead.maps_url:
        return lead.maps_url.split("?")[0].strip().lower()
    return f"{lead.name.strip().lower()}|{(lead.phone or lead.address).strip().lower()}"


def save_search_and_leads(query: str, country: str, city: str, leads: list[Lead]) -> None:
    """Persist search log and upsert captured leads into SQLite."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO search_logs (query, country, city, total_leads, created_at) VALUES (?, ?, ?, ?, ?)",
            (query, country, city, len(leads), now),
        )
        for lead in leads:
            key = _dedup_key(lead)
            conn.execute(
                """
                INSERT INTO leads (
                    dedup_key, name, category, rating, reviews, phone, website,
                    emails, email_status, address, facebook, instagram, linkedin,
                    maps_url, lead_score, priority, ai_summary, cold_email,
                    whatsapp_pitch, search_query, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dedup_key) DO UPDATE SET
                    emails = COALESCE(NULLIF(excluded.emails, ''), leads.emails),
                    email_status = COALESCE(NULLIF(excluded.email_status, ''), leads.email_status),
                    lead_score = COALESCE(NULLIF(excluded.lead_score, ''), leads.lead_score),
                    priority = COALESCE(NULLIF(excluded.priority, ''), leads.priority),
                    ai_summary = COALESCE(NULLIF(excluded.ai_summary, ''), leads.ai_summary),
                    cold_email = COALESCE(NULLIF(excluded.cold_email, ''), leads.cold_email),
                    whatsapp_pitch = COALESCE(NULLIF(excluded.whatsapp_pitch, ''), leads.whatsapp_pitch)
                """,
                (
                    key,
                    lead.name,
                    lead.category,
                    lead.rating,
                    lead.reviews,
                    lead.phone,
                    lead.website,
                    lead.emails,
                    lead.email_status,
                    lead.address,
                    lead.facebook,
                    lead.instagram,
                    lead.linkedin,
                    lead.maps_url,
                    lead.lead_score,
                    lead.priority,
                    lead.ai_summary,
                    lead.cold_email,
                    lead.whatsapp_pitch,
                    query,
                    now,
                ),
            )
        conn.commit()


def get_saved_leads(limit: int = 500) -> list[dict[str, Any]]:
    """Return recently saved leads from the database."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM leads ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_seen_urls() -> set[str]:
    """Return all saved Google Maps URLs for server-side duplicate skipping."""
    with _get_conn() as conn:
        rows = conn.execute("SELECT maps_url FROM leads WHERE maps_url != ''").fetchall()
    return {r["maps_url"].split("?")[0].strip().lower() for r in rows if r["maps_url"]}


def clear_saved_leads() -> None:
    """Clear all saved leads from the local database."""
    with _get_conn() as conn:
        conn.execute("DELETE FROM leads")
        conn.execute("DELETE FROM search_logs")
        conn.commit()
