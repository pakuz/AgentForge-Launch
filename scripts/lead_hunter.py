"""
lead_hunter.py — AgentForge
Scrapes and qualifies business leads from public sources, then uses AI
to score and summarize each lead.

Requirements:
    pip install httpx pydantic openai beautifulsoup4 python-dotenv rich

Usage:
    python lead_hunter.py --query "software agencies London" --max 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, HttpUrl, field_validator
from rich.console import Console
from rich.table import Table

load_dotenv()
console = Console()

# ── Configuration ────────────────────────────────────────────────────────────

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "YOUR_OPENAI_API_KEY_HERE")
SERPAPI_KEY    = os.getenv("SERPAPI_KEY", "YOUR_SERPAPI_KEY_HERE")   # optional
OUTPUT_FILE    = "leads_output.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}


# ── Data Models ──────────────────────────────────────────────────────────────

class Lead(BaseModel):
    name: str
    url: Optional[str] = None
    description: Optional[str] = None
    score: Optional[int] = Field(None, ge=0, le=100)
    score_reason: Optional[str] = None
    email_hint: Optional[str] = None
    scraped_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    @field_validator("score", mode="before")
    @classmethod
    def clamp_score(cls, v):
        if v is None:
            return v
        return max(0, min(100, int(v)))


class LeadBatch(BaseModel):
    query: str
    leads: list[Lead]
    generated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# ── Scraping ─────────────────────────────────────────────────────────────────

async def search_bing(query: str, max_results: int, client: httpx.AsyncClient) -> list[dict]:
    """Scrape Bing search results (no API key required)."""
    url = "https://www.bing.com/search"
    params = {"q": query, "count": max_results}
    results = []

    try:
        resp = await client.get(url, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for li in soup.select("li.b_algo")[:max_results]:
            title_tag = li.select_one("h2 a")
            desc_tag  = li.select_one(".b_caption p")
            if title_tag:
                results.append({
                    "name":        title_tag.get_text(strip=True),
                    "url":         title_tag.get("href"),
                    "description": desc_tag.get_text(strip=True) if desc_tag else "",
                })
    except httpx.HTTPError as exc:
        console.print(f"[red]HTTP error during search: {exc}[/red]")

    return results


async def fetch_page_text(url: str, client: httpx.AsyncClient) -> str:
    """Fetch and extract visible text from a URL (best-effort)."""
    try:
        resp = await client.get(url, headers=HEADERS, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)[:3000]
    except Exception:
        return ""


# ── AI Qualification ─────────────────────────────────────────────────────────

async def qualify_lead(lead_data: dict, query: str, client: AsyncOpenAI) -> Lead:
    """Ask GPT to score and qualify a raw lead."""
    system_prompt = (
        "You are a B2B sales qualification assistant. "
        "Given a search result and the original search query, evaluate how good a business lead it is. "
        "Return ONLY valid JSON with keys: score (0-100), score_reason (1 sentence), email_hint (domain-based guess or null)."
    )
    user_prompt = (
        f"Search query: {query}\n\n"
        f"Lead name: {lead_data.get('name')}\n"
        f"URL: {lead_data.get('url')}\n"
        f"Description: {lead_data.get('description', '')}\n"
        f"Page snippet: {lead_data.get('page_text', '')[:1000]}"
    )

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        data = json.loads(response.choices[0].message.content)
        return Lead(
            name=lead_data["name"],
            url=lead_data.get("url"),
            description=lead_data.get("description"),
            score=data.get("score"),
            score_reason=data.get("score_reason"),
            email_hint=data.get("email_hint"),
        )
    except Exception as exc:
        console.print(f"[yellow]AI qualification failed for '{lead_data['name']}': {exc}[/yellow]")
        return Lead(name=lead_data["name"], url=lead_data.get("url"), description=lead_data.get("description"))


# ── Display ──────────────────────────────────────────────────────────────────

def display_leads(batch: LeadBatch) -> None:
    table = Table(title=f"Leads for: {batch.query}", show_lines=True)
    table.add_column("Score", style="cyan", width=6)
    table.add_column("Name", style="bold white", max_width=30)
    table.add_column("URL", style="blue", max_width=40)
    table.add_column("Reason", max_width=40)

    for lead in sorted(batch.leads, key=lambda l: l.score or 0, reverse=True):
        score_str = str(lead.score) if lead.score is not None else "?"
        table.add_row(score_str, lead.name, lead.url or "", lead.score_reason or "")

    console.print(table)


# ── Main ─────────────────────────────────────────────────────────────────────

async def main(query: str, max_results: int) -> None:
    console.print(f"[bold green]🔍 Lead Hunter starting…[/bold green] Query: [cyan]{query}[/cyan]")

    openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    async with httpx.AsyncClient() as http:
        console.print("Scraping search results…")
        raw_leads = await search_bing(query, max_results, http)
        console.print(f"Found [bold]{len(raw_leads)}[/bold] raw results. Fetching pages…")

        # Fetch page text concurrently (best-effort)
        page_tasks = [fetch_page_text(r.get("url", ""), http) for r in raw_leads]
        page_texts = await asyncio.gather(*page_tasks, return_exceptions=True)
        for i, text in enumerate(page_texts):
            raw_leads[i]["page_text"] = text if isinstance(text, str) else ""

    console.print("Qualifying leads with AI…")
    qualify_tasks = [qualify_lead(r, query, openai_client) for r in raw_leads]
    leads = list(await asyncio.gather(*qualify_tasks))

    batch = LeadBatch(query=query, leads=leads)
    display_leads(batch)

    with open(OUTPUT_FILE, "w") as f:
        f.write(batch.model_dump_json(indent=2))
    console.print(f"\n[green]Saved {len(leads)} leads → {OUTPUT_FILE}[/green]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AgentForge Lead Hunter")
    parser.add_argument("--query", required=True, help="Search query for leads")
    parser.add_argument("--max",   type=int, default=15, help="Maximum results to scrape")
    args = parser.parse_args()

    if OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
        console.print("[red]Error: Set OPENAI_API_KEY in your .env file or environment.[/red]")
        sys.exit(1)

    asyncio.run(main(args.query, args.max))
