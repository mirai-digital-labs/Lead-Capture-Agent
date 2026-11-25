"""Lead Capture Agent — Extract Google Maps businesses + website emails to Excel & CSV."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from exporter import export_leads
from scraper.email_extractor import enrich_leads
from scraper.maps_scraper import fetch_maps_leads

console = Console()


async def run_agent(query: str, limit: int, headless: bool, skip_emails: bool) -> int:
    load_dotenv()

    mode = "Serper API" if os.getenv("SERPER_API_KEY", "").strip() else "Free Browser Scraper (No API Key)"
    console.print(
        Panel.fit(
            f"[bold cyan]Lead Capture Agent[/bold cyan]\n"
            f"Query: [yellow]{query}[/yellow]\n"
            f"Max Leads: [green]{limit}[/green] | Mode: [magenta]{mode}[/magenta]",
            border_style="cyan",
        )
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        maps_task = progress.add_task("[cyan]Searching Google Maps...", total=limit)

        def on_maps_progress(idx: int, total: int, name: str) -> None:
            progress.update(maps_task, total=total, completed=idx, description=f"[cyan]Maps: {name[:35]}")

        leads = await fetch_maps_leads(
            query=query,
            limit=limit,
            headless=headless,
            on_progress=on_maps_progress,
        )
        progress.update(maps_task, completed=len(leads), total=max(len(leads), 1))

    if not leads:
        console.print("[bold red]No leads found for this query. Try a broader search or --show-browser.[/bold red]")
        return 1

    console.print(f"[green]Found {len(leads)} businesses on Google Maps.[/green]")

    if not skip_emails:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            email_task = progress.add_task("[yellow]Scanning websites for emails...", total=len(leads))

            def on_email_progress(name: str) -> None:
                progress.advance(email_task, 1)
                progress.update(email_task, description=f"[yellow]Website: {name[:35]}")

            await enrich_leads(leads, on_progress=on_email_progress)

    csv_path, xlsx_path = export_leads(leads, query=query)

    table = Table(title="Captured Leads Preview (Top 10)", show_lines=True)
    table.add_column("Business", style="cyan", max_width=28)
    table.add_column("Phone", style="green", max_width=18)
    table.add_column("Emails", style="yellow", max_width=28)
    table.add_column("Website", style="blue", max_width=28)

    for lead in leads[:10]:
        table.add_row(lead.name, lead.phone or "-", lead.emails or "-", lead.website or "-")

    console.print(table)

    emails_found = sum(1 for l in leads if l.emails)
    console.print(
        Panel.fit(
            f"[bold green]Done![/bold green]\n"
            f"Total Leads: [bold]{len(leads)}[/bold] | Leads with Email: [bold]{emails_found}[/bold]\n"
            f"Excel: [underline]{xlsx_path.resolve()}[/underline]\n"
            f"CSV:   [underline]{csv_path.resolve()}[/underline]",
            border_style="green",
        )
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Google Maps + Website Email Lead Capture Agent")
    parser.add_argument("-q", "--query", type=str, help='Search query, e.g. "Dentists in Lahore"')
    parser.add_argument("-l", "--limit", type=int, default=20, help="Max number of leads (default: 20)")
    parser.add_argument("--show-browser", action="store_true", help="Show browser window while scraping")
    parser.add_argument("--skip-emails", action="store_true", help="Skip website email extraction")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    query = args.query
    limit = args.limit

    if not query:
        console.print("[bold cyan]Lead Capture Agent (Interactive Mode)[/bold cyan]")
        query = console.input("[yellow]Enter search query (e.g., Dentists in Lahore): [/yellow]").strip()
        if not query:
            console.print("[red]Search query cannot be empty.[/red]")
            sys.exit(1)
        limit_raw = console.input("[yellow]How many leads? (default 20): [/yellow]").strip()
        if limit_raw.isdigit() and int(limit_raw) > 0:
            limit = int(limit_raw)

    exit_code = asyncio.run(
        run_agent(
            query=query,
            limit=limit,
            headless=not args.show_browser,
            skip_emails=args.skip_emails,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
