"""
inbox_triager.py — AgentForge
Connects to a Gmail inbox via IMAP, categorises unread emails with AI,
and optionally drafts replies for high-priority messages.

Requirements:
    pip install httpx pydantic openai python-dotenv rich

Gmail setup:
    1. Enable IMAP in Gmail settings.
    2. Create an App Password: myaccount.google.com → Security → App Passwords
    3. Set GMAIL_USER and GMAIL_APP_PASSWORD in .env

Usage:
    python inbox_triager.py                    # triage 20 recent emails
    python inbox_triager.py --limit 50 --draft  # also draft replies
"""

from __future__ import annotations

import argparse
import asyncio
import email
import imaplib
import json
import os
import sys
from datetime import datetime
from email.header import decode_header
from typing import Literal, Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from rich.console import Console
from rich.table import Table

load_dotenv()
console = Console()

OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY",    "YOUR_OPENAI_API_KEY_HERE")
GMAIL_USER        = os.getenv("GMAIL_USER",        "your@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "YOUR_GMAIL_APP_PASSWORD")
IMAP_HOST         = "imap.gmail.com"
MODEL             = "gpt-4o-mini"
OUTPUT_FILE       = "inbox_triage.json"

CATEGORIES = Literal[
    "urgent",
    "needs_reply",
    "newsletter",
    "notification",
    "spam",
    "internal",
    "sales_lead",
    "support",
    "other",
]

# ── Data Models ──────────────────────────────────────────────────────────────

class EmailItem(BaseModel):
    uid: str
    subject: str
    sender: str
    date: Optional[str] = None
    snippet: str
    category: Optional[str] = None
    priority: Optional[int] = Field(None, ge=1, le=5, description="1=highest")
    summary: Optional[str] = None
    draft_reply: Optional[str] = None
    action: Optional[str] = None


class TriageReport(BaseModel):
    email_account: str
    processed: int
    emails: list[EmailItem]
    generated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# ── IMAP Helpers ──────────────────────────────────────────────────────────────

def decode_str(value: str | bytes | None) -> str:
    """Decode potentially encoded email header strings."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def extract_body(msg: email.message.Message) -> str:
    """Extract plain-text body from an email message."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    body += payload.decode("utf-8", errors="replace")
                    break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode("utf-8", errors="replace")
    return body[:2000]  # cap for AI


def fetch_emails(limit: int) -> list[EmailItem]:
    """Fetch recent unread emails via IMAP."""
    items: list[EmailItem] = []

    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        imap.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        imap.select("INBOX")

        _, data = imap.search(None, "UNSEEN")
        uids = data[0].split()
        uids = uids[-limit:]  # most recent N

        console.print(f"Found [bold]{len(uids)}[/bold] unread emails. Fetching…")

        for uid in uids:
            _, raw = imap.fetch(uid, "(RFC822)")
            if not raw or not raw[0]:
                continue
            raw_email = raw[0][1]
            if not isinstance(raw_email, bytes):
                continue

            msg = email.message_from_bytes(raw_email)
            items.append(EmailItem(
                uid=uid.decode(),
                subject=decode_str(msg.get("Subject", "(no subject)")),
                sender=decode_str(msg.get("From", "")),
                date=decode_str(msg.get("Date", "")),
                snippet=extract_body(msg)[:300],
            ))
            # Attach full body for AI processing
            items[-1].__dict__["_full_body"] = extract_body(msg)

        imap.logout()
    except imaplib.IMAP4.error as exc:
        console.print(f"[red]IMAP error: {exc}[/red]")
        raise

    return items


# ── AI Triage ─────────────────────────────────────────────────────────────────

TRIAGE_SYSTEM = """You are an email triage assistant for a small business.
Analyse the email and return JSON with:
- category: one of urgent|needs_reply|newsletter|notification|spam|internal|sales_lead|support|other
- priority: 1-5 (1=highest, 5=lowest)
- summary: one sentence explaining the email
- action: what the user should do (e.g. "Reply by EOD", "Unsubscribe", "Archive")
Return ONLY valid JSON."""

DRAFT_SYSTEM = """You are a professional email assistant.
Write a concise, polite reply to the email below.
Match the sender's formality level. Keep it under 150 words.
Return only the reply body text, no subject line."""


async def triage_email(item: EmailItem, draft: bool, client: AsyncOpenAI) -> EmailItem:
    body = item.__dict__.get("_full_body", item.snippet)
    user_content = (
        f"Subject: {item.subject}\n"
        f"From: {item.sender}\n"
        f"Date: {item.date}\n\n"
        f"{body}"
    )

    # --- Categorise ---
    try:
        resp = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": TRIAGE_SYSTEM},
                {"role": "user",   "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        data = json.loads(resp.choices[0].message.content)
        item.category = data.get("category")
        item.priority  = data.get("priority")
        item.summary   = data.get("summary")
        item.action    = data.get("action")
    except Exception as exc:
        console.print(f"[yellow]Triage failed for '{item.subject}': {exc}[/yellow]")

    # --- Draft reply if requested and warranted ---
    if draft and item.category in {"urgent", "needs_reply", "sales_lead", "support"}:
        try:
            resp2 = await client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": DRAFT_SYSTEM},
                    {"role": "user",   "content": user_content},
                ],
                temperature=0.5,
                max_tokens=300,
            )
            item.draft_reply = resp2.choices[0].message.content.strip()
        except Exception as exc:
            console.print(f"[yellow]Draft failed: {exc}[/yellow]")

    return item


# ── Display ──────────────────────────────────────────────────────────────────

CATEGORY_COLORS = {
    "urgent":       "bold red",
    "needs_reply":  "bold yellow",
    "sales_lead":   "bold green",
    "support":      "cyan",
    "newsletter":   "dim",
    "notification": "dim",
    "spam":         "red dim",
    "internal":     "blue",
    "other":        "white",
}


def display_report(report: TriageReport, show_drafts: bool) -> None:
    table = Table(title=f"Inbox Triage — {report.email_account}", show_lines=True)
    table.add_column("P", width=3)
    table.add_column("Category", width=14)
    table.add_column("Subject", max_width=35)
    table.add_column("From", max_width=25)
    table.add_column("Action", max_width=30)

    sorted_emails = sorted(report.emails, key=lambda e: e.priority or 99)
    for item in sorted_emails:
        color = CATEGORY_COLORS.get(item.category or "other", "white")
        table.add_row(
            str(item.priority or "?"),
            f"[{color}]{item.category or '?'}[/{color}]",
            item.subject[:35],
            item.sender[:25],
            item.action or "",
        )

    console.print(table)

    if show_drafts:
        for item in sorted_emails:
            if item.draft_reply:
                console.rule(f"[bold]Draft Reply: {item.subject[:50]}[/bold]")
                console.print(item.draft_reply)
                console.print()


# ── Main ─────────────────────────────────────────────────────────────────────

async def main(limit: int, draft: bool) -> None:
    console.print("[bold green]📬 Inbox Triager starting…[/bold green]")

    if OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
        console.print("[red]Set OPENAI_API_KEY in .env[/red]")
        sys.exit(1)
    if GMAIL_APP_PASSWORD == "YOUR_GMAIL_APP_PASSWORD":
        console.print("[red]Set GMAIL_USER and GMAIL_APP_PASSWORD in .env[/red]")
        sys.exit(1)

    emails = fetch_emails(limit)
    if not emails:
        console.print("[yellow]No unread emails found.[/yellow]")
        return

    ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    console.print(f"Triaging [bold]{len(emails)}[/bold] emails…")

    tasks = [triage_email(e, draft, ai_client) for e in emails]
    triaged = list(await asyncio.gather(*tasks))

    report = TriageReport(
        email_account=GMAIL_USER,
        processed=len(triaged),
        emails=triaged,
    )

    display_report(report, show_drafts=draft)

    with open(OUTPUT_FILE, "w") as f:
        f.write(report.model_dump_json(indent=2))
    console.print(f"\n[green]Report saved → {OUTPUT_FILE}[/green]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AgentForge Inbox Triager")
    parser.add_argument("--limit", type=int, default=20, help="Max emails to triage")
    parser.add_argument("--draft", action="store_true", help="Draft replies for important emails")
    args = parser.parse_args()
    asyncio.run(main(args.limit, args.draft))
