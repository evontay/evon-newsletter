# MosAIc Pulse

A weekly industry intelligence digest for UX/DesignOps teams, auto-generated with Claude AI.

Collects articles from RSS feeds and your Gmail inbox, scores them for relevance, synthesises a structured digest, and emails it to you every Friday — ready to review and forward to your team.

---

## What it does

1. **Collects** articles from 34 RSS sources (UX, AI, design leadership, horizon signals, YouTube) and newsletters/LinkedIn emails from your Gmail inbox
2. **Filters** by date (last 30 days), deduplicates, and applies per-domain caps
3. **Scores** editorial items with Claude Haiku for relevance — YouTube videos are scored separately to guarantee one video pick per week
4. **Synthesises** a structured digest with Claude Sonnet, written for a UX/DesignOps team navigating AI transition
5. **Emails** a styled HTML digest, ready to review before forwarding

---

## Digest format

- **This Week's Theme** — one sentence connecting the week's signals
- **Week in Context** — 80–100 word editorial prose
- **01 | Craft & Practice** ✏️
- **02 | Tools & AI Fluency** ⚡
- **03 | Design Leadership** 🧭
- **04 | Horizon Watch** 🔭
- **05 | Video of the Week** ▶️ ← always present, scored separately
- **06 | Skills Market Signals** 💼
- **Skill of the Week** 🌱
- **Reflection Prompt** 💭

---

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/evontay/evon-newsletter.git
cd evon-newsletter
pip install -r requirements.txt
```

### 2. Create your `.env` file

```bash
cp .env.example .env
```

Fill in the values:

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | From [console.anthropic.com](https://console.anthropic.com) |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `465` |
| `SMTP_USER` | Your Gmail address |
| `SMTP_PASSWORD` | A [Gmail App Password](https://myaccount.google.com/apppasswords) |
| `TO_EMAIL` | Where to send the digest |

### 3. Set up Gmail inbox integration (optional)

To pull newsletters and LinkedIn emails from your inbox:

1. Create a project at [console.cloud.google.com](https://console.cloud.google.com)
2. Enable the **Gmail API**
3. Create **OAuth 2.0 credentials** (Desktop app) and download as `credentials.json` into this folder
4. Add your Gmail address as a test user under **APIs & Services → Audience**
5. On first run, a browser window will open for one-time authorisation — after that it runs silently

### 4. Run it

```bash
python mosaic_pulse.py
```

### 5. Schedule for every Friday at 8am Singapore time (UTC+8)

```bash
crontab -e
```

Add:

```
0 0 * * 5 cd /path/to/evon-newsletter && python3 mosaic_pulse.py >> pulse.log 2>&1
```

---

## Adding sources

To add a new RSS feed, add one line to `sources.py`:

```python
{"url": "https://example.com/feed", "name": "Source Name"},
```

No other file needs editing.

---

## Files

| File | Description |
|---|---|
| `mosaic_pulse.py` | Main script |
| `sources.py` | RSS feed list |
| `requirements.txt` | Python dependencies |
| `.env.example` | Environment variable template |
| `.gitignore` | Keeps secrets out of git |

> **Never commit** `.env`, `credentials.json`, or `token.json` — these contain your secrets and are excluded by `.gitignore`.
