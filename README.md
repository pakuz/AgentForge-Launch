# 🤖 AgentForge — AI Automation Suite for Small Businesses

AgentForge is a collection of production-ready Python automation scripts that help small businesses save time on repetitive, high-value tasks using modern AI APIs.

---

## 📦 Scripts Overview

| Script | What it does |
|--------|-------------|
| `lead_hunter.py` | Scrapes web search results, fetches company pages, and AI-qualifies each lead with a 0–100 score |
| `content_repurpose.py` | Takes a blog post (URL or file) and generates optimised content for Twitter/X, LinkedIn, Instagram, and newsletters |
| `inbox_triager.py` | Connects to your Gmail inbox, categorises unread emails by urgency/type, and optionally drafts replies |
| `competitor_monitor.py` | Watches competitor websites for price/feature changes and delivers AI-analysed change reports |
| `meeting_distiller.py` | Transforms meeting transcripts into structured action items, decisions, summaries, and open questions |

---

## 🚀 Quick Start

### 1. Clone / copy the scripts

```bash
git clone https://github.com/your-org/agentforge.git
cd agentforge/scripts
```

### 2. Install dependencies

```bash
pip install httpx pydantic openai beautifulsoup4 python-dotenv rich
```

> **Python 3.10+** required.

### 3. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env` with your API keys (see below).

---

## 🔑 Environment Variables

Create a `.env` file in the `scripts/` directory:

```env
# Required for all AI-powered scripts
OPENAI_API_KEY=sk-...your-openai-key...

# Required for inbox_triager.py
GMAIL_USER=you@gmail.com
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx

# Optional: SerpAPI for lead_hunter.py (Bing scraping is used by default)
SERPAPI_KEY=your-serpapi-key
```

### Getting your API keys

- **OpenAI:** [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
- **Gmail App Password:** [myaccount.google.com → Security → App Passwords](https://myaccount.google.com/apppasswords) (requires 2FA enabled)
- **SerpAPI** (optional): [serpapi.com](https://serpapi.com)

---

## 📖 Script Usage

### 🔍 lead_hunter.py

Finds and qualifies business leads from web searches.

```bash
# Basic search
python lead_hunter.py --query "marketing agencies NYC"

# More results
python lead_hunter.py --query "SaaS companies Berlin" --max 30
```

**Output:** Console table + `leads_output.json`

**Customisation:**
- Edit `SERPAPI_KEY` to use SerpAPI instead of Bing scraping for more reliable results
- Adjust the `qualify_lead` prompt to match your ideal customer profile (ICP)

---

### 📝 content_repurpose.py

Converts blog posts to platform-ready social content.

```bash
# Repurpose a URL for all platforms
python content_repurpose.py --url https://yourblog.com/article

# Only Twitter and LinkedIn, save to files
python content_repurpose.py --url https://... --formats twitter linkedin --save

# Use a local text file
python content_repurpose.py --file article.txt --formats newsletter

# Available platforms: twitter, linkedin, instagram, newsletter
```

**Output:** Rich console display, optional files in `repurposed_content/`

---

### 📬 inbox_triager.py

Categorises and prioritises your Gmail inbox.

```bash
# Triage last 20 unread emails
python inbox_triager.py

# Triage 50 emails and draft replies for important ones
python inbox_triager.py --limit 50 --draft
```

**Categories assigned:** `urgent`, `needs_reply`, `sales_lead`, `support`, `newsletter`, `notification`, `spam`, `internal`, `other`

**Output:** Priority table + `inbox_triage.json`

> ⚠️ **Gmail setup:** Enable IMAP in Gmail settings → Forwarding and POP/IMAP. Create an App Password (not your regular password).

---

### 👁️ competitor_monitor.py

Tracks competitor website changes over time.

```bash
# Add competitors and take baseline snapshot
python competitor_monitor.py --add "https://competitor.com/pricing" "Acme Corp"
python competitor_monitor.py --add "https://rival.io/features" "Rival SaaS"

# Run again later to detect changes
python competitor_monitor.py
```

**First run:** Captures baseline snapshots (no diff report yet).  
**Subsequent runs:** Diffs against baseline, highlights changes with AI analysis.

**Output:** Change report table + `competitor_report.json`, baseline stored in `competitor_baseline.json`

> 💡 **Tip:** Schedule with cron to run daily: `0 9 * * * cd /path/to/scripts && python competitor_monitor.py`

---

### 🎙️ meeting_distiller.py

Extracts action items from meeting transcripts.

```bash
# Process a plain text transcript
python meeting_distiller.py --file transcript.txt

# Process a WebVTT subtitle file (from Zoom, Teams, etc.)
python meeting_distiller.py --file meeting.vtt

# Pipe from stdin
cat transcript.txt | python meeting_distiller.py --stdin

# Output formats: rich (default), json, markdown
python meeting_distiller.py --file transcript.txt --output markdown

# Save to file
python meeting_distiller.py --file transcript.txt --save notes.md
```

**Output:** Action items table, decisions, open questions, executive summary

**Supported formats:**
- `.txt` — plain text transcripts
- `.vtt` — WebVTT (Zoom, Google Meet, Teams auto-captions)

---

## 🛠️ Dependencies

| Package | Purpose |
|---------|---------|
| `openai` | GPT-4o AI capabilities |
| `httpx` | Async HTTP client |
| `pydantic` | Data validation & modelling |
| `beautifulsoup4` | HTML parsing |
| `python-dotenv` | `.env` file loading |
| `rich` | Beautiful terminal output |

Install all at once:
```bash
pip install httpx pydantic openai beautifulsoup4 python-dotenv rich
```

---

## 🏗️ Architecture

All scripts follow the same design principles:

- **Async-first** — concurrent I/O with `asyncio` + `httpx`
- **Pydantic models** — validated, typed data throughout
- **Graceful error handling** — network failures don't crash the run
- **JSON output** — machine-readable results for downstream use
- **Rich terminal UI** — clear, colourful output for humans
- **Environment-based config** — no hardcoded secrets

---

## 🔒 Security Notes

- Never commit your `.env` file — add it to `.gitignore`
- Gmail App Passwords are revocable — create one per app
- OpenAI API calls cost money — set usage limits in your OpenAI dashboard
- Web scraping should respect `robots.txt` and rate limits

---

## 📈 Recommended Workflow

1. **Morning:** Run `inbox_triager.py` to prioritise your inbox
2. **Daily:** Run `competitor_monitor.py` to catch pricing changes
3. **Weekly:** Run `lead_hunter.py` for new prospect research
4. **After meetings:** Run `meeting_distiller.py` on your transcript
5. **After publishing:** Run `content_repurpose.py` to repurpose content

---

## 📄 License

MIT — use freely, modify as needed.

---

*Built with ❤️ by AgentForge — AI automation that actually ships.*
