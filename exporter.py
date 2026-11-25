"""Export captured leads to deduplicated CSV and styled Excel (.xlsx) files."""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from scraper.maps_scraper import Lead

COLUMNS = [
    ("name", "Business Name"),
    ("category", "Category"),
    ("phone", "Phone"),
    ("emails", "Emails"),
    ("email_status", "Email Status"),
    ("website", "Website"),
    ("rating", "Rating"),
    ("reviews", "Reviews"),
    ("address", "Address"),
    ("facebook", "Facebook"),
    ("instagram", "Instagram"),
    ("linkedin", "LinkedIn"),
    ("maps_url", "Google Maps URL"),
    ("lead_score", "Lead Score"),
    ("priority", "Priority"),
    ("ai_summary", "AI Insight"),
    ("cold_email", "Cold Email"),
    ("whatsapp_pitch", "WhatsApp Pitch"),
]


def deduplicate_leads(leads: list[Lead]) -> list[Lead]:
    """Remove duplicate leads by normalized (name, phone/address)."""
    seen: set[tuple[str, str]] = set()
    unique: list[Lead] = []
    for lead in leads:
        key = (lead.name.lower().strip(), (lead.phone or lead.address).lower().strip())
        if key not in seen:
            seen.add(key)
            unique.append(lead)
    return unique


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    return slug[:40] or "leads"


def export_leads(leads: list[Lead], query: str, output_dir: Path | str = "output") -> tuple[Path, Path]:
    """Write leads to both CSV and styled Excel files, returning (csv_path, xlsx_path)."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"{_slugify(query)}_{timestamp}"
    csv_path = out_path / f"{base_name}.csv"
    xlsx_path = out_path / f"{base_name}.xlsx"

    clean_leads = deduplicate_leads(leads)

    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([header for _, header in COLUMNS])
        for lead in clean_leads:
            data = lead.to_dict()
            writer.writerow([data.get(field, "") for field, _ in COLUMNS])

    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"

    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=11)

    ws.append([header for _, header in COLUMNS])
    for col_idx in range(1, len(COLUMNS) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for lead in clean_leads:
        data = lead.to_dict()
        ws.append([data.get(field, "") for field, _ in COLUMNS])

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{len(clean_leads) + 1}"

    for col_idx, (_, header) in enumerate(COLUMNS, start=1):
        max_len = len(header)
        for row_idx in range(2, len(clean_leads) + 2):
            val = str(ws.cell(row=row_idx, column=col_idx).value or "")
            if len(val) > max_len:
                max_len = len(val)
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 4, 45)

    wb.save(xlsx_path)
    return csv_path, xlsx_path
