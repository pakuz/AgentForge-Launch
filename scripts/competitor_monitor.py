"""
competitor_monitor.py — AgentForge
Tracks competitor websites for price and feature changes.
On each run, compares against a stored baseline and highlights diffs with AI analysis.

Requirements:
    pip install httpx pydantic openai beautifulsoup4 python-dotenv rich

Usage:
    # First run: builds baseline
    python competitor_monitor.py

    # Subsequent runs: diffs against baseline and shows AI analysis
    python competitor_monitor.py

    # Add a competitor on the fly
    python competitor_monitor.py --add "https://competitor.com" "Competitor Name"
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv()
console = Console()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "YOUR_OPENAI_API_KEY_HERE")
MODEL          = "gpt-4o-mini"
BASELINE_FILE  = "competitor_baseline.json"
OUTPUT_FILE    = "competitor_report.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    )
}

# Default competitors — edit or use --add
DEFAULT_COMPETITORS: list[dict] = [
    # {"name": "Acme Corp",   "url": "https://acmecorp.com/pricing"},
    # {"name": "BetaSaaS",    "url": "https://betasaas.io/pricing"},
]


# ── Data Models ──────────────────────────────────────────────────────────────

class Competitor(BaseModel):
    name: str
    url: str


class SnapshotEntry(BaseModel):
    name: str
    url: str
    text_hash: str
    text: str
    captured_at: str


class ChangeReport(BaseModel):
    name: str
    url: str
    previous_captured: Optional[str] = None
    current_captured: str
    changed: bool
    diff_preview: Optional[str] = None
    ai_analysis: Optional[str] = None


class MonitorReport(BaseModel):
    run_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    changes: list[ChangeReport]
    no_changes: list[str] = []


class Baseline(BaseModel):
    snapshots: dict[str, SnapshotEntry] = {}   # keyed by URL
    competitors: list[Competitor] = []


# ── Scraping ─────────────────────────────────────────────────────────────────

def clean_text(html: str) -> str:
    """Extract meaningful text from HTML, removing boilerplate."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


async def fetch_competitor(comp: Competitor, client: httpx.AsyncClient) -> Optional[SnapshotEntry]:
    try:
        resp = await client.get(comp.url, headers=HEADERS, timeout=20, follow_redirects=True)
        resp.raise_for_status()
        text = clean_text(resp.text)
        text_hash = hashlib.sha256(text.encode()).hexdigest()
        return SnapshotEntry(
            name=comp.name,
            url=comp.url,
            text=text[:8000],
            text_hash=text_hash,
            captured_at=datetime.utcnow().isoformat(),
        )
    except httpx.HTTPError as exc:
        console.print(f"[red]Failed to fetch {comp.name} ({comp.url}): {exc}[/red]")
        return None


# ── Diff & Analysis ───────────────────────────────────────────────────────────

def make_diff(old_text: str, new_text: str, context: int = 5) -> str:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=context))
    return "\n".join(diff[:150])  # cap output


AI_ANALYSIS_PROMPT = """You are a competitive intelligence analyst.
A competitor's pricing/features page has changed. Analyse the diff below and return a concise summary:
- What specifically changed? (prices, features, plans, CTAs, messaging)
- Is this change significant or minor?
- What action should the business take in response?

Keep your answer under 200 words. Be specific."""


async def analyse_change(comp_name: str, diff: str, client: AsyncOpenAI) -> str:
    try:
        resp = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": AI_ANALYSIS_PROMPT},
                {"role": "user",   "content": f"Competitor: {comp_name}\n\nDiff:\n{diff}"},
            ],
            temperature=0.3,
            max_tokens=400,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        return f"[Analysis failed: {exc}]"


# ── Baseline I/O ──────────────────────────────────────────────────────────────

def load_baseline() -> Baseline:
    p = Path(BASELINE_FILE)
    if p.exists():
        return Baseline.model_validate_json(p.read_text())
    return Baseline()


def save_baseline(baseline: Baseline) -> None:
    Path(BASELINE_FILE).write_text(baseline.model_dump_json(indent=2))


# ── Display ──────────────────────────────────────────────────────────────────

def display_report(report: MonitorReport) -> None:
    if not report.changes:
        console.print("[green]✅ No changes detected for any competitor.[/green]")
        return

    table = Table(title="Competitor Changes Detected", show_lines=True)
    table.add_column("Competitor", style="bold white")
    table.add_column("URL", style="blue")
    table.add_column("Last Seen")
    table.add_column("Changed?", justify="center")

    for cr in report.changes:
        table.add_row(
            cr.name,
            cr.url,
            cr.previous_captured or "—",
            "[bold red]YES[/bold red]",
        )
    for name in report.no_changes:
        table.add_row(name, "", "", "[green]No[/green]")

    console.print(table)

    for cr in report.changes:
        console.print(Panel(
            cr.ai_analysis or "No analysis available.",
            title=f"[bold yellow]Analysis: {cr.name}[/bold yellow]",
            border_style="yellow",
        ))


# ── Main ─────────────────────────────────────────────────────────────────────

async def main(add_name: Optional[str], add_url: Optional[str]) -> None:
    console.print("[bold green]🔍 Competitor Monitor starting…[/bold green]")

    baseline = load_baseline()

    # Seed defaults if no competitors yet
    if not baseline.competitors:
        for c in DEFAULT_COMPETITORS:
            baseline.competitors.append(Competitor(**c))

    # Add new competitor if requested
    if add_name and add_url:
        existing_urls = {c.url for c in baseline.competitors}
        if add_url not in existing_urls:
            baseline.competitors.append(Competitor(name=add_name, url=add_url))
            console.print(f"[green]Added competitor: {add_name}[/green]")

    if not baseline.competitors:
        console.print(
            "[yellow]No competitors configured. Edit DEFAULT_COMPETITORS in the script "
            "or use --add 'URL' 'Name'.[/yellow]"
        )
        sys.exit(0)

    # Fetch current snapshots
    console.print(f"Fetching {len(baseline.competitors)} competitor pages…")
    async with httpx.AsyncClient() as http:
        tasks = [fetch_competitor(c, http) for c in baseline.competitors]
        snapshots = await asyncio.gather(*tasks)

    ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY != "YOUR_OPENAI_API_KEY_HERE" else None
    changes: list[ChangeReport] = []
    no_changes: list[str] = []

    for snap in snapshots:
        if snap is None:
            continue
        prev = baseline.snapshots.get(snap.url)

        if prev is None:
            # First time — just store
            console.print(f"[cyan]Baseline captured for {snap.name}[/cyan]")
            baseline.snapshots[snap.url] = snap
        elif prev.text_hash == snap.text_hash:
            no_changes.append(snap.name)
            # Update snapshot timestamp but not content
            baseline.snapshots[snap.url] = snap
        else:
            console.print(f"[yellow]Change detected: {snap.name}[/yellow]")
            diff = make_diff(prev.text, snap.text)
            analysis = None
            if ai_client:
                analysis = await analyse_change(snap.name, diff, ai_client)

            changes.append(ChangeReport(
                name=snap.name,
                url=snap.url,
                previous_captured=prev.captured_at,
                current_captured=snap.captured_at,
                changed=True,
                diff_preview=diff[:2000],
                ai_analysis=analysis,
            ))
            baseline.snapshots[snap.url] = snap

    save_baseline(baseline)

    report = MonitorReport(changes=changes, no_changes=no_changes)
    display_report(report)

    Path(OUTPUT_FILE).write_text(report.model_dump_json(indent=2))
    console.print(f"\n[green]Report saved → {OUTPUT_FILE}[/green]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AgentForge Competitor Monitor")
    parser.add_argument("--add", nargs=2, metavar=("URL", "NAME"),
                        help="Add a competitor URL and name")
    args = parser.parse_args()

    add_url, add_name = (args.add[0], args.add[1]) if args.add else (None, None)
    asyncio.run(main(add_name, add_url))
