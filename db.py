"""Persistent lead, search log, user account, and credit storage (SQLite)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auth import generate_session_token, hash_password, verify_password
from scraper.maps_scraper import Lead

DATA_DIR = Path(__file__).resolve().parent / "data"
SQLITE_PATH = DATA_DIR / "leads.db"


def _get_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables for users, sessions, leads, and search logs."""
    with _get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                credits INTEGER NOT NULL DEFAULT 50,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 0,
                dedup_key TEXT NOT NULL,
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
                created_at TEXT,
                UNIQUE(user_id, dedup_key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS search_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL DEFAULT 0,
                query TEXT,
                country TEXT,
                city TEXT,
                total_leads INTEGER,
                created_at TEXT
            )
            """
        )
        conn.commit()


def create_user(email: str, password: str) -> tuple[str, dict[str, Any]]:
    """Register a user (first user becomes admin with 99,999 credits) and return (token, user_dict)."""
    norm_email = email.strip().lower()
    pw_hash, salt = hash_password(password)
    now = datetime.now(timezone.utc).isoformat()

    with _get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        is_admin = 1 if count == 0 else 0
        credits = 99999 if is_admin else 50
        try:
            cur = conn.execute(
                "INSERT INTO users (email, password_hash, salt, credits, is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (norm_email, pw_hash, salt, credits, is_admin, now),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Email already registered") from exc

        user_id = cur.lastrowid
        token = generate_session_token()
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, user_id, now),
        )
        conn.commit()

    return token, {"id": user_id, "email": norm_email, "credits": credits, "is_admin": bool(is_admin)}


def authenticate_user(email: str, password: str) -> tuple[str, dict[str, Any]] | None:
    """Verify credentials and return (token, user_dict), or None if invalid."""
    norm_email = email.strip().lower()
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (norm_email,)).fetchone()
        if not row or not verify_password(password, row["password_hash"], row["salt"]):
            return None
        token = generate_session_token()
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, row["id"], now),
        )
        conn.commit()
    return token, {
        "id": row["id"],
        "email": row["email"],
        "credits": row["credits"],
        "is_admin": bool(row["is_admin"]),
    }


def get_user_by_token(token: str) -> dict[str, Any] | None:
    """Look up a user from a bearer session token."""
    if not token:
        return None
    with _get_conn() as conn:
        row = conn.execute(
            """
            SELECT u.id, u.email, u.credits, u.is_admin
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
            """,
            (token,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "email": row["email"],
        "credits": row["credits"],
        "is_admin": bool(row["is_admin"]),
    }


def deduct_credits(user_id: int, amount: int) -> int:
    """Deduct credits after a successful capture and return remaining balance."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE users SET credits = MAX(0, credits - ?) WHERE id = ?",
            (max(0, amount), user_id),
        )
        conn.commit()
        row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
    return int(row["credits"]) if row else 0


def add_user_credits(email: str, amount: int) -> int | None:
    """Add credits to a user account by email; returns new balance or None if not found."""
    norm_email = email.strip().lower()
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE users SET credits = credits + ? WHERE email = ?",
            (amount, norm_email),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT credits FROM users WHERE email = ?", (norm_email,)).fetchone()
    return int(row["credits"]) if row else None


def _dedup_key(lead: Lead) -> str:
    if lead.maps_url:
        return lead.maps_url.split("?")[0].strip().lower()
    return f"{lead.name.strip().lower()}|{(lead.phone or lead.address).strip().lower()}"


def save_search_and_leads(user_id: int, query: str, country: str, city: str, leads: list[Lead]) -> None:
    """Persist search log and upsert captured leads for a user."""
    now = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO search_logs (user_id, query, country, city, total_leads, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, query, country, city, len(leads), now),
        )
        for lead in leads:
            key = _dedup_key(lead)
            conn.execute(
                """
                INSERT INTO leads (
                    user_id, dedup_key, name, category, rating, reviews, phone, website,
                    emails, email_status, address, facebook, instagram, linkedin,
                    maps_url, lead_score, priority, ai_summary, cold_email,
                    whatsapp_pitch, search_query, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, dedup_key) DO UPDATE SET
                    emails = COALESCE(NULLIF(excluded.emails, ''), leads.emails),
                    email_status = COALESCE(NULLIF(excluded.email_status, ''), leads.email_status),
                    lead_score = COALESCE(NULLIF(excluded.lead_score, ''), leads.lead_score),
                    priority = COALESCE(NULLIF(excluded.priority, ''), leads.priority),
                    ai_summary = COALESCE(NULLIF(excluded.ai_summary, ''), leads.ai_summary),
                    cold_email = COALESCE(NULLIF(excluded.cold_email, ''), leads.cold_email),
                    whatsapp_pitch = COALESCE(NULLIF(excluded.whatsapp_pitch, ''), leads.whatsapp_pitch)
                """,
                (
                    user_id,
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


def get_saved_leads(user_id: int, limit: int = 500) -> list[dict[str, Any]]:
    """Return recently saved leads for a user."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM leads WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_seen_urls(user_id: int) -> set[str]:
    """Return all saved Google Maps URLs for a user."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT maps_url FROM leads WHERE user_id = ? AND maps_url != ''",
            (user_id,),
        ).fetchall()
    return {r["maps_url"].split("?")[0].strip().lower() for r in rows if r["maps_url"]}


def clear_saved_leads(user_id: int) -> None:
    """Clear all saved leads for a user."""
    with _get_conn() as conn:
        conn.execute("DELETE FROM leads WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM search_logs WHERE user_id = ?", (user_id,))
        conn.commit()
