"""
meeting_distiller.py — AgentForge
Extracts structured action items, decisions, and summaries from meeting
transcripts (plain text, .txt, or .vtt subtitle files).

Requirements:
    pip install openai pydantic python-dotenv rich

Usage:
    python meeting_distiller.py --file transcript.txt
    python meeting_distiller.py --file meeting.vtt --output json
    echo "Meeting text..." | python meeting_distiller.py --stdin
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv()
console = Console()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "YOUR_OPENAI_API_KEY_HERE")
MODEL          = "gpt-4o"
MAX_CHUNK      = 12000  # chars per chunk for large transcripts


# ── Data Models ──────────────────────────────────────────────────────────────

class ActionItem(BaseModel):
    task: str
    owner: Optional[str] = None
    due_date: Optional[str] = None
    priority: Literal["high", "medium", "low"] = "medium"


class Decision(BaseModel):
    decision: str
    context: Optional[str] = None


class MeetingDistillation(BaseModel):
    meeting_title: Optional[str] = None
    meeting_date: Optional[str] = None
    participants: list[str] = []
    executive_summary: str
    key_topics: list[str] = []
    action_items: list[ActionItem] = []
    decisions: list[Decision] = []
    open_questions: list[str] = []
    next_meeting: Optional[str] = None
    processed_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# ── Transcript Parsing ────────────────────────────────────────────────────────

VTT_TIMESTAMP_RE = re.compile(r"^\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[.,]\d{3}")
NOTE_TAG_RE      = re.compile(r"<[^>]+>")


def parse_vtt(text: str) -> str:
    """Strip WebVTT timestamps and tags, return clean transcript text."""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if VTT_TIMESTAMP_RE.match(line):
            continue
        if line in ("WEBVTT", "") or line.startswith("NOTE") or "-->" in line:
            continue
        clean = NOTE_TAG_RE.sub("", line)
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def load_transcript(path: Optional[str], from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.read()
    if not path:
        console.print("[red]Provide --file or --stdin[/red]")
        sys.exit(1)
    p = Path(path)
    if not p.exists():
        console.print(f"[red]File not found: {path}[/red]")
        sys.exit(1)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".vtt":
        text = parse_vtt(text)
    return text


# ── AI Processing ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert meeting analyst. 
Extract structured information from the meeting transcript.
Return ONLY valid JSON matching this schema exactly:

{
  "meeting_title": "string or null",
  "meeting_date": "string or null",
  "participants": ["name1", "name2"],
  "executive_summary": "2-3 sentence summary",
  "key_topics": ["topic1", "topic2"],
  "action_items": [
    {
      "task": "description",
      "owner": "person name or null",
      "due_date": "date or null",
      "priority": "high|medium|low"
    }
  ],
  "decisions": [
    {
      "decision": "what was decided",
      "context": "why or additional context"
    }
  ],
  "open_questions": ["question1"],
  "next_meeting": "date/time or null"
}

Be thorough with action items — these are the most important output.
If something is unclear, make a reasonable inference."""


def distil_chunk(chunk: str, client: OpenAI) -> dict:
    """Process a single transcript chunk."""
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": f"Transcript:\n\n{chunk}"},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    return json.loads(resp.choices[0].message.content)


def merge_chunks(results: list[dict]) -> dict:
    """Merge multiple chunk results into one consolidated distillation."""
    if len(results) == 1:
        return results[0]

    merged = results[0].copy()
    for r in results[1:]:
        merged["action_items"].extend(r.get("action_items", []))
        merged["decisions"].extend(r.get("decisions", []))
        merged["open_questions"].extend(r.get("open_questions", []))
        merged["participants"] = list(set(merged["participants"] + r.get("participants", [])))
        merged["key_topics"] = list(set(merged["key_topics"] + r.get("key_topics", [])))

    # Deduplicate action items by task text (case-insensitive)
    seen = set()
    unique_actions = []
    for a in merged["action_items"]:
        key = a.get("task", "").lower()[:50]
        if key not in seen:
            seen.add(key)
            unique_actions.append(a)
    merged["action_items"] = unique_actions

    return merged


def process_transcript(text: str, client: OpenAI) -> MeetingDistillation:
    """Chunk-process a transcript and return a MeetingDistillation."""
    # Split into chunks for large transcripts
    chunks = [text[i:i+MAX_CHUNK] for i in range(0, len(text), MAX_CHUNK)]
    console.print(f"Processing transcript in [bold]{len(chunks)}[/bold] chunk(s)…")

    results = []
    for i, chunk in enumerate(chunks, 1):
        console.print(f"  Chunk {i}/{len(chunks)}…")
        try:
            results.append(distil_chunk(chunk, client))
        except Exception as exc:
            console.print(f"[red]Chunk {i} failed: {exc}[/red]")

    if not results:
        raise RuntimeError("All chunks failed to process.")

    merged = merge_chunks(results)
    return MeetingDistillation(**merged)


# ── Display ──────────────────────────────────────────────────────────────────

PRIORITY_COLORS = {"high": "bold red", "medium": "yellow", "low": "dim"}


def display_distillation(d: MeetingDistillation) -> None:
    title = d.meeting_title or "Meeting Distillation"
    console.print(Panel(
        d.executive_summary,
        title=f"[bold cyan]{title}[/bold cyan]",
        border_style="cyan",
    ))

    if d.participants:
        console.print(f"[bold]Participants:[/bold] {', '.join(d.participants)}")
    if d.meeting_date:
        console.print(f"[bold]Date:[/bold] {d.meeting_date}")
    if d.key_topics:
        console.print(f"[bold]Topics:[/bold] {', '.join(d.key_topics)}")
    console.print()

    # Action items table
    if d.action_items:
        table = Table(title="✅ Action Items", show_lines=True)
        table.add_column("Priority", width=8)
        table.add_column("Task")
        table.add_column("Owner", width=15)
        table.add_column("Due", width=12)

        for item in sorted(d.action_items, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x.priority]):
            color = PRIORITY_COLORS[item.priority]
            table.add_row(
                f"[{color}]{item.priority.upper()}[/{color}]",
                item.task,
                item.owner or "—",
                item.due_date or "—",
            )
        console.print(table)

    # Decisions
    if d.decisions:
        console.print("\n[bold]📋 Decisions Made:[/bold]")
        for dec in d.decisions:
            console.print(f"  • [bold]{dec.decision}[/bold]")
            if dec.context:
                console.print(f"    [dim]{dec.context}[/dim]")

    # Open questions
    if d.open_questions:
        console.print("\n[bold]❓ Open Questions:[/bold]")
        for q in d.open_questions:
            console.print(f"  • {q}")

    if d.next_meeting:
        console.print(f"\n[bold]📅 Next Meeting:[/bold] {d.next_meeting}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="AgentForge Meeting Distiller")
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--file",  help="Transcript file (.txt or .vtt)")
    src.add_argument("--stdin", action="store_true", help="Read transcript from stdin")
    parser.add_argument(
        "--output",
        choices=["rich", "json", "markdown"],
        default="rich",
        help="Output format",
    )
    parser.add_argument("--save", metavar="FILE", help="Save output to file")
    args = parser.parse_args()

    if OPENAI_API_KEY == "YOUR_OPENAI_API_KEY_HERE":
        console.print("[red]Set OPENAI_API_KEY in .env[/red]")
        sys.exit(1)

    transcript = load_transcript(args.file, args.stdin)
    console.print(f"[bold green]🎙️  Meeting Distiller[/bold green]  ({len(transcript)} chars)")

    client = OpenAI(api_key=OPENAI_API_KEY)
    distillation = process_transcript(transcript, client)

    if args.output == "rich":
        display_distillation(distillation)
    elif args.output == "json":
        print(distillation.model_dump_json(indent=2))
    elif args.output == "markdown":
        md = _to_markdown(distillation)
        print(md)

    if args.save:
        ext = Path(args.save).suffix.lower()
        if ext == ".json":
            Path(args.save).write_text(distillation.model_dump_json(indent=2))
        elif ext in (".md", ".markdown"):
            Path(args.save).write_text(_to_markdown(distillation))
        else:
            Path(args.save).write_text(distillation.model_dump_json(indent=2))
        console.print(f"[green]Saved → {args.save}[/green]")


def _to_markdown(d: MeetingDistillation) -> str:
    lines = [f"# {d.meeting_title or 'Meeting Notes'}"]
    if d.meeting_date:
        lines.append(f"\n**Date:** {d.meeting_date}")
    if d.participants:
        lines.append(f"**Participants:** {', '.join(d.participants)}")
    lines.append(f"\n## Summary\n\n{d.executive_summary}")

    if d.action_items:
        lines.append("\n## Action Items\n")
        lines.append("| Priority | Task | Owner | Due |")
        lines.append("|----------|------|-------|-----|")
        for a in d.action_items:
            lines.append(f"| {a.priority.upper()} | {a.task} | {a.owner or '—'} | {a.due_date or '—'} |")

    if d.decisions:
        lines.append("\n## Decisions\n")
        for dec in d.decisions:
            lines.append(f"- **{dec.decision}**")
            if dec.context:
                lines.append(f"  _{dec.context}_")

    if d.open_questions:
        lines.append("\n## Open Questions\n")
        for q in d.open_questions:
            lines.append(f"- {q}")

    if d.next_meeting:
        lines.append(f"\n## Next Meeting\n\n{d.next_meeting}")

    lines.append(f"\n---\n_Distilled by AgentForge at {d.processed_at}_")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
