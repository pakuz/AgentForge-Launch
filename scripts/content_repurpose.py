"""
content_repurpose.py — AgentForge
Transforms a blog post (URL or raw text) into platform-optimised social
media content: Twitter/X thread, LinkedIn post, Instagram caption, and
a short-form newsletter blurb.

Requirements:
    pip install httpx pydantic openai beautifulsoup4 python-dotenv rich

Usage:
    python content_repurpose.py --url https://example.com/my-blog-post
    python content_repurpose.py --file article.txt --formats twitter linkedin
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

load_dotenv()
console = Console()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "YOUR_OPENAI_API_KEY_HERE")
MODEL = "gpt-4o"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; AgentForge/1.0; +https://agentforge.io)"
    )
}

PLATFORM_PROMPTS: dict[str, str] = {
    "twitter": (
        "Create a Twitter/X thread from the article below. "
        "Format as a numbered list of tweets (max 280 chars each). "
        "Start with a hook tweet. Include 3-7 tweets total. "
        "End with a call-to-action tweet. Add relevant hashtags to the last tweet only."
    ),
    "linkedin": (
        "Write a LinkedIn post based on the article below. "
        "Professional but personable tone. 150-300 words. "
        "Start with a bold hook line (no 'I' at the start). "
        "Use short paragraphs. Include 3-5 relevant hashtags at the end. "
        "End with a question to drive engagement."
    ),
    "instagram": (
        "Write an Instagram caption based on the article below. "
        "Engaging, conversational, 100-150 words. "
        "Include a strong opening line (first 125 chars matter most). "
        "Add a clear CTA ('link in bio'). "
        "End with 10-15 relevant hashtags separated by line breaks."
    ),
    "newsletter": (
        "Write a short newsletter blurb (80-120 words) summarising the article below. "
        "Casual, friendly tone. Briefly describe what readers will learn. "
        "End with a CTA linking to the full article. "
        "No hashtags. Just clean prose."
    ),
}


# ── Data Models ──────────────────────────────────────────────────────────────

class ContentOutput(BaseModel):
    platform: str
    content: str
    char_count: int = Field(default=0)

    def model_post_init(self, __context) -> None:
        self.char_count = len(self.content)


class RepurposeResult(BaseModel):
    source_title: Optional[str] = None
    source_url:   Optional[str] = None
    outputs: list[ContentOutput] = []


# ── Article Extraction ────────────────────────────────────────────────────────

async def fetch_article(url: str, client: httpx.AsyncClient) -> tuple[str, str]:
    """Return (title, body_text) from a URL."""
    try:
        resp = await client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Failed to fetch URL: {exc}") from exc

    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.title.string.strip() if soup.title else "Unknown Title"

    # Remove boilerplate
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()

    # Prefer <article> element, fall back to <body>
    article = soup.find("article") or soup.find("main") or soup.body
    text = article.get_text(separator="\n", strip=True) if article else soup.get_text()

    # Trim to a reasonable token budget (~6000 chars)
    return title, text[:6000]


def read_file(path: str) -> tuple[str, str]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return p.stem, p.read_text(encoding="utf-8")[:6000]


# ── AI Generation ─────────────────────────────────────────────────────────────

async def generate_for_platform(
    platform: str,
    article_text: str,
    client: AsyncOpenAI,
) -> ContentOutput:
    prompt = PLATFORM_PROMPTS[platform]

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user",   "content": f"Article:\n\n{article_text}"},
            ],
            temperature=0.7,
            max_tokens=800,
        )
        content = response.choices[0].message.content.strip()
    except Exception as exc:
        console.print(f"[red]Generation failed for {platform}: {exc}[/red]")
        content = f"[Generation failed: {exc}]"

    return ContentOutput(platform=platform, content=content)


# ── Display ──────────────────────────────────────────────────────────────────

PLATFORM_COLORS = {
    "twitter":    "bright_cyan",
    "linkedin":   "bright_blue",
    "instagram":  "magenta",
    "newsletter": "green",
}


def display_result(result: RepurposeResult) -> None:
    console.print(Rule(f"[bold]Content for: {result.source_title or 'Article'}[/bold]"))
    for output in result.outputs:
        color = PLATFORM_COLORS.get(output.platform, "white")
        console.print(
            Panel(
                output.content,
                title=f"[bold {color}]{output.platform.upper()}[/bold {color}]"
                      f"  [{output.char_count} chars]",
                border_style=color,
                padding=(1, 2),
            )
        )


def save_result(result: RepurposeResult, output_dir: str = ".") -> None:
    base = Path(output_dir)
    base.mkdir(parents=True, exist_ok=True)
    slug = (result.source_title or "repurposed").lower().replace(" ", "_")[:40]

    for output in result.outputs:
        filename = base / f"{slug}_{output.platform}.txt"
        filename.write_text(output.content, encoding="utf-8")
        console.print(f"[dim]Saved → {filename}[/dim]")


# ── Main ─────────────────────────────────────────────────────────────────────

async def main(
    url: Optional[str],
    file: Optional[str],
    formats: list[str],
    save: bool,
) -> None:
    if not url and not file:
        console.print("[red]Provide --url or --file.[/red]")
        sys.exit(1)

    # Validate requested platforms
    valid = set(PLATFORM_PROMPTS.keys())
    bad = set(formats) - valid
    if bad:
        console.print(f"[red]Unknown platforms: {bad}. Valid: {valid}[/red]")
        sys.exit(1)

    ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    if url:
        console.print(f"[bold green]📄 Fetching article:[/bold green] {url}")
        async with httpx.AsyncClient() as http:
            title, text = await fetch_article(url, http)
    else:
        console.print(f"[bold green]📄 Reading file:[/bold green] {file}")
        title, text = read_file(file)

    console.print(f"Article: [cyan]{title}[/cyan]  ({len(text)} chars)\n")
    console.print(f"Generating content for: [bold]{', '.join(formats)}[/bold]…")

    tasks = [generate_for_platform(p, text, ai_client) for p in formats]
    outputs = list(await asyncio.gather(*tasks))

    result = RepurposeResult(
        source_title=title,
        source_url=url,
        outputs=outputs,
    )

    display_result(result)

    if save:
        save_result(result, output_dir="repurposed_content")

    console.print("\n[bold green]✅ Done![/bold green]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AgentForge Content Repurposer")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--url",  help="URL of the blog post to repurpose")
    src.add_argument("--file", help="Local text file with the article")
    parser.add_argument(
        "--formats",
        nargs="+",
        default=list(PLATFORM_PROMPTS.keys()),
        help=f"Platforms to generate for. Choices: {list(PLATFORM_PROMPTS.keys())}",
    )
    parser.add_argument("--save", action="store_true", help="Save outputs to files")
    args = parser.parse_args()

    if OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
        console.print("[red]Error: Set OPENAI_API_KEY in .env[/red]")
        sys.exit(1)

    asyncio.run(main(args.url, args.file, args.formats, args.save))
