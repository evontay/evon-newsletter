#!/usr/bin/env python3
"""
MosAIc Pulse — Weekly industry intelligence digest for UX/DesignOps teams.
Run with: python mosaic_pulse.py
"""

import os
import re
import json
import smtplib
import logging
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.parse import urlparse

import base64
import email as email_lib

import feedparser
import anthropic
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from sources import SOURCES

# ── Setup ──────────────────────────────────────────────────────────────────────

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
TO_EMAIL = os.getenv("TO_EMAIL")

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-20250514"

PRIORITY_UNCAPPED_DOMAINS = {
    "petermerholz.com",
    "proofofconcept.pub",
    "eflowers.substack.com",
    "muledesign.com",
    "matthewstrom.com",
    "joulee.medium.com",
    "ben-evans.com",
    "oneusefulthing.org",
    "aisnakeoil.com",
}

JOB_DOMAINS = {"indeed.com", "academyux.com", "linkedin.com"}

FEED_TIMEOUT = 10
MAX_ITEMS_TO_HAIKU = 20
MAX_EDITORIAL_ITEMS = 10
MAX_JOB_ITEMS = 2
LOOKBACK_DAYS = 30

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
GMAIL_CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")
GMAIL_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")
GMAIL_LOOKBACK_DAYS = 7  # Only pull inbox items from the last 7 days


# ── Step 1: Collect ────────────────────────────────────────────────────────────

def strip_html(text: str) -> str:
    if not text:
        return ""
    return BeautifulSoup(text, "html.parser").get_text(separator=" ").strip()


def parse_date(entry):
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def fetch_feed(source: dict) -> list[dict]:
    url = source["url"]
    name = source["name"]
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": "MosAIcPulse/1.0"})
        items = []
        for entry in feed.entries:
            title = getattr(entry, "title", None)
            link = getattr(entry, "link", None)
            if not title or not link:
                continue
            author = getattr(entry, "author", "")
            pub_date = parse_date(entry)
            raw_summary = getattr(entry, "summary", "") or ""
            if not raw_summary and hasattr(entry, "content"):
                raw_summary = entry.content[0].value if entry.content else ""
            snippet = strip_html(raw_summary)
            items.append({
                "title": title,
                "url": link,
                "author": author,
                "pub_date": pub_date,
                "snippet": snippet,
                "source_name": name,
            })
        return items
    except Exception as e:
        log.warning(f"Feed failed [{name}]: {e}")
        return []


def collect_all() -> list[dict]:
    log.info(f"Fetching {len(SOURCES)} feeds in parallel...")
    all_items = []
    success = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(fetch_feed, s): s for s in SOURCES}
        for future in as_completed(futures):
            items = future.result()
            if items:
                success += 1
                all_items.extend(items)
            else:
                failed += 1

    log.info(f"Feeds: {success} succeeded, {failed} failed — {len(all_items)} raw items")
    return all_items


# ── Step 1b: Gmail Collect ────────────────────────────────────────────────────

def get_gmail_service():
    creds = None
    if os.path.exists(GMAIL_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE, GMAIL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(GMAIL_CREDENTIALS_FILE, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(GMAIL_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def extract_email_text(payload: dict) -> str:
    """Recursively extract plain text or HTML from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if mime_type == "text/plain" and body_data:
        return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="ignore")

    if mime_type == "text/html" and body_data:
        html = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="ignore")
        return strip_html(html)

    # Multipart — recurse into parts
    for part in payload.get("parts", []):
        text = extract_email_text(part)
        if text:
            return text

    return ""


def collect_from_gmail() -> list[dict]:
    if not os.path.exists(GMAIL_CREDENTIALS_FILE):
        log.info("No Gmail credentials found — skipping inbox collection")
        return []

    try:
        service = get_gmail_service()
    except Exception as e:
        log.warning(f"Gmail auth failed: {e}")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=GMAIL_LOOKBACK_DAYS)
    after_ts = int(cutoff.timestamp())

    # Search for newsletters and LinkedIn emails
    query = (
        f"after:{after_ts} "
        f"(category:promotions OR from:linkedin.com OR from:notifications@linkedin.com "
        f"OR from:jobs-listings@linkedin.com OR subject:newsletter OR subject:digest) "
        f"-is:sent"
    )

    try:
        result = service.users().messages().list(
            userId="me", q=query, maxResults=50
        ).execute()
    except Exception as e:
        log.warning(f"Gmail search failed: {e}")
        return []

    messages = result.get("messages", [])
    if not messages:
        log.info("Gmail: no matching emails found")
        return []

    log.info(f"Gmail: found {len(messages)} matching emails")
    items = []

    for msg_ref in messages:
        try:
            msg = service.users().messages().get(
                userId="me", id=msg_ref["id"], format="full"
            ).execute()

            headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
            subject = headers.get("subject", "").strip()
            sender = headers.get("from", "").strip()
            date_str = headers.get("date", "")

            if not subject:
                continue

            # Parse sender name
            sender_name = sender.split("<")[0].strip().strip('"') or sender

            # Parse date
            pub_date = None
            try:
                parsed = email_lib.utils.parsedate_to_datetime(date_str)
                pub_date = parsed.astimezone(timezone.utc)
            except Exception:
                pub_date = datetime.now(timezone.utc)

            # Extract body text
            body_text = extract_email_text(msg["payload"])
            snippet = " ".join(body_text.split())[:300] if body_text else msg.get("snippet", "")

            # Use Gmail message URL as the "link"
            msg_url = f"https://mail.google.com/mail/u/0/#inbox/{msg_ref['id']}"

            items.append({
                "title": subject,
                "url": msg_url,
                "author": sender_name,
                "pub_date": pub_date,
                "snippet": snippet,
                "source_name": sender_name,
            })

        except Exception as e:
            log.warning(f"Gmail: failed to parse message {msg_ref['id']}: {e}")
            continue

    log.info(f"Gmail: extracted {len(items)} items from inbox")
    return items


# ── Step 2: Filter ─────────────────────────────────────────────────────────────

UTM_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"}


def normalise_url(url: str) -> str:
    """Strip UTM tracking parameters so the same article always maps to the same key."""
    try:
        from urllib.parse import urlparse, urlencode, parse_qsl
        parsed = urlparse(url)
        clean_qs = urlencode([(k, v) for k, v in parse_qsl(parsed.query) if k not in UTM_PARAMS])
        return parsed._replace(query=clean_qs).geturl()
    except Exception:
        return url


def get_domain(url: str) -> str:
    try:
        hostname = urlparse(url).hostname or ""
        parts = hostname.lstrip("www.").split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else hostname
    except Exception:
        return ""


def is_job_item(url: str) -> bool:
    domain = get_domain(url)
    return any(jd in domain for jd in JOB_DOMAINS)


def load_used_urls() -> set:
    """Load all article URLs previously featured in past newsletters."""
    try:
        files = github_store.list_directory("archive")
        for name, content in files:
            if name == "used_urls.json":
                return set(json.loads(content))
    except Exception as e:
        log.warning(f"Could not load used URLs: {e}")
    return set()


def save_used_urls(new_urls: list, existing: set) -> None:
    """Append newly featured URLs to the persistent used-URLs list."""
    updated = sorted(existing | set(new_urls))
    try:
        github_store.write_file(
            "archive/used_urls.json",
            json.dumps(updated, indent=2),
            "Update used article URLs",
        )
    except Exception as e:
        log.warning(f"Could not save used URLs: {e}")


def filter_items(items: list[dict], used_urls: set = None) -> tuple[list[dict], list[dict], list[dict]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    # Date filter
    dated = [i for i in items if i["pub_date"] and i["pub_date"] >= cutoff]
    log.info(f"After date filter (last {LOOKBACK_DAYS} days): {len(dated)} items")

    # Deduplicate by URL, and exclude previously featured articles
    # Use normalised URLs (UTM params stripped) for comparison
    previously_seen = used_urls or set()
    seen_urls = set()
    deduped = []
    for item in dated:
        key = normalise_url(item["url"])
        if key not in seen_urls and key not in previously_seen:
            seen_urls.add(key)
            deduped.append(item)
    log.info(f"After dedup + archive exclusion: {len(deduped)} items")

    # Separate jobs, YouTube, and editorial
    editorial = []
    youtube = []
    jobs = []
    for item in deduped:
        if is_job_item(item["url"]):
            jobs.append(item)
        elif "youtube.com" in item["url"]:
            youtube.append(item)
        else:
            editorial.append(item)

    # Per-domain cap on editorial only (max 2, except priority sources)
    domain_counts: dict[str, int] = {}
    capped = []
    for item in editorial:
        domain = get_domain(item["url"])
        if domain in PRIORITY_UNCAPPED_DOMAINS:
            capped.append(item)
        else:
            count = domain_counts.get(domain, 0)
            if count < 2:
                domain_counts[domain] = count + 1
                capped.append(item)

    log.info(
        f"After domain capping: {len(capped)} editorial, "
        f"{len(youtube)} YouTube, {len(jobs)} job items"
    )

    # Cap editorial at MAX_ITEMS_TO_HAIKU
    capped = capped[:MAX_ITEMS_TO_HAIKU]
    log.info(f"Sending {len(capped)} editorial items to Haiku")

    return capped, youtube, jobs


# ── Step 3: Haiku Relevance Filter ────────────────────────────────────────────

def truncate(text: str, max_len: int) -> str:
    return text[:max_len] if text else ""


def score_with_haiku(items: list[dict]) -> list[dict]:
    if not items:
        return []

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    payload = []
    for idx, item in enumerate(items, 1):
        is_yt = "youtube.com" in item["url"]
        payload.append({
            "index": idx,
            "title": truncate(item["title"], 100),
            "snippet": truncate(item["snippet"], 100),
            "url": item["url"],
            "is_youtube": is_yt,
        })

    system_prompt = (
        "Relevance filter for a UX/DesignOps team. Score each item 1-3. "
        "Priority: (1) AI-ready UX workflows, (2) design leadership, "
        "(3) UX research methods, (4) DesignOps maturity. "
        "YouTube URLs get +1 bonus capped at 3. "
        "Assign stream: craft | tools | leadership | horizon. "
        'Return ONLY valid JSON array: [{"index":1,"score":3,"stream":"tools","reason":"one sentence"}]'
    )

    user_prompt = json.dumps(payload, ensure_ascii=False)

    try:
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = response.content[0].text.strip()
        # Extract JSON array from response
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not match:
            raise ValueError("No JSON array found in Haiku response")
        scores = json.loads(match.group())

        score_map = {s["index"]: s for s in scores}
        for idx, item in enumerate(items, 1):
            meta = score_map.get(idx, {})
            item["score"] = meta.get("score", 1)
            item["stream"] = meta.get("stream", "craft")
            item["reason"] = meta.get("reason", "")

        items.sort(key=lambda x: x.get("score", 0), reverse=True)
        top = items[:MAX_EDITORIAL_ITEMS]
        log.info(f"Haiku scored {len(items)} items — keeping top {len(top)}")
        return top

    except Exception as e:
        log.warning(f"Haiku scoring failed ({e}) — falling back to first {MAX_EDITORIAL_ITEMS} items unscored")
        for item in items[:MAX_EDITORIAL_ITEMS]:
            item["score"] = 1
            item["stream"] = "craft"
            item["reason"] = ""
        return items[:MAX_EDITORIAL_ITEMS]


def score_youtube_with_haiku(items: list[dict]) -> list[dict]:
    """Score YouTube items separately and return the single best one."""
    if not items:
        return []

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    payload = []
    for idx, item in enumerate(items, 1):
        payload.append({
            "index": idx,
            "title": truncate(item["title"], 100),
            "snippet": truncate(item["snippet"], 100),
            "channel": item["source_name"],
        })

    system_prompt = (
        "Relevance filter for a UX/DesignOps team reviewing YouTube videos. "
        "Score each video 1-3 for relevance to: AI-ready UX workflows, design leadership, "
        "UX research methods, or DesignOps maturity. "
        "Return ONLY valid JSON array: "
        '[{"index":1,"score":3,"reason":"one sentence on why this video is valuable"}]'
    )

    try:
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=1000,
            system=system_prompt,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
        raw = response.content[0].text.strip()
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not match:
            raise ValueError("No JSON array in YouTube Haiku response")
        scores = json.loads(match.group())

        score_map = {s["index"]: s for s in scores}
        for idx, item in enumerate(items, 1):
            meta = score_map.get(idx, {})
            item["score"] = meta.get("score", 1)
            item["stream"] = "youtube"
            item["reason"] = meta.get("reason", "")

        items.sort(key=lambda x: x.get("score", 0), reverse=True)
        best = items[:1]
        log.info(f"Haiku scored {len(items)} YouTube items — selected: {best[0]['title'][:60]}")
        return best

    except Exception as e:
        log.warning(f"YouTube Haiku scoring failed ({e}) — falling back to most recent video")
        items[0]["stream"] = "youtube"
        items[0]["score"] = 1
        items[0]["reason"] = ""
        return items[:1]


# ── Step 4: Sonnet Synthesis ───────────────────────────────────────────────────

def build_digest_with_sonnet(editorial: list[dict], youtube: list[dict], jobs: list[dict]) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    final_items = editorial + youtube[:1] + jobs[:MAX_JOB_ITEMS]

    today_str = datetime.now().strftime("%d %B %Y")

    item_lines = []
    for item in final_items:
        stream = item.get("stream", "craft")
        is_yt = "youtube.com" in item["url"]
        if stream == "jobs":
            label = "[JOBS]"
        elif is_yt:
            label = "[YOUTUBE]"
        else:
            label = f"[{stream.upper()}]"
        item_lines.append(
            f"{label} {item['source_name']} — {item['title']}\n"
            f"  URL: {item['url']}\n"
            f"  Snippet: {truncate(item['snippet'], 300)}\n"
        )

    items_text = "\n".join(item_lines)

    system_prompt = (
        "You are an industry intelligence assistant for Evon Tay, a senior UX/DesignOps people manager in Singapore. "
        "She leads a 5-person team inside an AI-focused organisation (MosAIc AI Experience, under APEX Chief AI Office). "
        "The team is transitioning from conventional UX practice toward AI-ready workflows. "
        "Two priority skill gaps: (1) AI-ready workflows and fluency, (2) design leadership — strategy, facilitation, advocacy, systems thinking. "
        "Produce a weekly MosAIc Pulse digest. Be specific and practitioner-level. No generic observations. "
        "If source material is thin for a section, note it briefly — do not pad. "
        "The WEEK IN CONTEXT section should read as analytical prose — not a list. "
        "Write it as a short editorial: what is the field collectively saying this week, what tensions or patterns emerge across the signals, "
        "and what does it mean for a team navigating AI transition. Aim for 80-100 words, field-note register. "
        "Total digest under 700 words."
    )

    user_prompt = f"""Here are this week's curated articles. Produce the MosAIc Pulse digest exactly in this format:

MOSAIC PULSE — Week of {today_str}

THIS WEEK'S THEME: [one sharp sentence connecting the week's signals]

WEEK IN CONTEXT
[80-100 words of analytical prose synthesising the week's signals into a coherent narrative.]

01 | CRAFT & PRACTICE
[2 items max — skip section if no relevant articles]
• [Source name] — [1-line summary] -> [URL]
  ↳ Why it matters: [1 sentence, specific to AI UX or DesignOps context]

02 | TOOLS & AI FLUENCY
[2 items max]
• [Source name] — [1-line summary] -> [URL]
  ↳ Try this: [one concrete low-lift action the team can take]

03 | DESIGN LEADERSHIP
[2 items max]
• [Source name] — [1-line summary] -> [URL]
  ↳ Leadership angle: [1 sentence on strategic implication]

04 | HORIZON WATCH
[1 item — the most forward-looking signal in the set]
• [Source name] — [1-line summary] -> [URL]
  ↳ 6-month implication: [one specific projection]

05 | VIDEO OF THE WEEK
[1 item — pick the single best [YOUTUBE] item; skip section if no YouTube items in source material]
• [Channel name] — [1-line description of what the video covers] -> [URL]
  ↳ Watch for: [one sentence on the specific insight or skill it builds]
  ↳ Runtime value: [one sentence on why it's worth 10-20 minutes of the team's time]

06 | SKILLS MARKET SIGNALS
[2 items max — skip section if no job postings in source material]
• [Job title] at [Company] -> [URL]
  ↳ What this demands: [2-3 specific skills or competencies]
  ↳ Team implication: [one sentence on skill development meaning]

SKILL OF THE WEEK
[Skill name] — [why relevant now, 1 sentence] — [one free resource -> URL]

REFLECTION PROMPT
[One thought-provoking question for team discussion — not generic]

---

SOURCE MATERIAL:
{items_text}"""

    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=2000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text.strip()


# ── Step 5: Email ──────────────────────────────────────────────────────────────

SECTION_COLORS = {
    "01": "#4A6CF7",  # Craft & Practice — blue
    "02": "#7C3AED",  # Tools & AI Fluency — purple
    "03": "#059669",  # Design Leadership — green
    "04": "#D97706",  # Horizon Watch — amber
    "05": "#E11D48",  # Video of the Week — crimson
    "06": "#DC2626",  # Skills Market — red
}

SECTION_ICONS = {
    "01": "✏️",
    "02": "⚡",
    "03": "🧭",
    "04": "🔭",
    "05": "▶️",
    "06": "💼",
}

SPECIAL_ICONS = {
    "SKILL OF THE WEEK": "🌱",
    "REFLECTION PROMPT": "💭",
}


def digest_to_html(digest: str, timestamp: str) -> str:
    lines = digest.splitlines()
    html_parts = []

    def esc(text):
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def linkify(text):
        # Convert -> URL or plain URLs to hyperlinks
        text = esc(text)
        text = re.sub(
            r'-&gt;\s*(https?://\S+)',
            r'<a href="\1" style="color:#4A6CF7;text-decoration:none;">→ link</a>',
            text
        )
        text = re.sub(
            r'(?<!["\'])(https?://\S+)',
            r'<a href="\1" style="color:#4A6CF7;text-decoration:none;">\1</a>',
            text
        )
        return text

    in_context_block = False

    for line in lines:
        stripped = line.strip()

        # Main title
        if stripped.startswith("MOSAIC PULSE —"):
            parts = stripped.split("—", 1)
            html_parts.append(
                f'<h1 style="margin:0 0 4px 0;font-size:26px;font-weight:800;'
                f'letter-spacing:-0.5px;color:#111;">'
                f'MosAIc Pulse</h1>'
                f'<p style="margin:0 0 24px 0;font-size:13px;color:#888;'
                f'text-transform:uppercase;letter-spacing:1px;">'
                f'{esc(parts[1].strip()) if len(parts) > 1 else ""}</p>'
            )

        # Theme line
        elif stripped.startswith("THIS WEEK'S THEME:"):
            theme = stripped[len("THIS WEEK'S THEME:"):].strip()
            html_parts.append(
                f'<div style="background:#F0F4FF;border-left:4px solid #4A6CF7;'
                f'padding:14px 16px;margin:0 0 24px 0;border-radius:0 6px 6px 0;">'
                f'<span style="font-size:11px;font-weight:700;color:#4A6CF7;'
                f'text-transform:uppercase;letter-spacing:1px;">This Week\'s Theme</span>'
                f'<p style="margin:6px 0 0 0;font-size:15px;font-weight:600;'
                f'color:#111;line-height:1.5;">{esc(theme)}</p>'
                f'</div>'
            )

        # Section headers like "WEEK IN CONTEXT"
        elif stripped == "WEEK IN CONTEXT":
            in_context_block = True
            html_parts.append(
                f'<h2 style="margin:0 0 10px 0;font-size:11px;font-weight:700;'
                f'text-transform:uppercase;letter-spacing:2px;color:#888;">'
                f'🗞️ &nbsp;Week in Context</h2>'
            )
            html_parts.append('<p style="margin:0 0 28px 0;font-size:14px;line-height:1.8;color:#333;">')

        # Numbered section headers like "01 | CRAFT & PRACTICE"
        elif re.match(r'^\d{2} \|', stripped):
            if in_context_block:
                html_parts.append('</p>')
                in_context_block = False
            num = stripped[:2]
            label = stripped[5:].strip()
            color = SECTION_COLORS.get(num, "#4A6CF7")
            icon = SECTION_ICONS.get(num, "")
            html_parts.append(
                f'<div style="border-top:2px solid {color};margin:28px 0 14px 0;'
                f'padding-top:14px;display:flex;align-items:center;">'
                f'<span style="font-size:18px;margin-right:10px;line-height:1;">{icon}</span>'
                f'<span style="font-size:11px;font-weight:700;color:{color};'
                f'text-transform:uppercase;letter-spacing:2px;">{num}</span>'
                f'<span style="font-size:15px;font-weight:700;color:#111;'
                f'margin-left:8px;">{esc(label)}</span>'
                f'</div>'
            )

        # Bullet items
        elif stripped.startswith("•"):
            if in_context_block:
                html_parts.append('</p>')
                in_context_block = False
            content = stripped[1:].strip()
            html_parts.append(
                f'<p style="margin:0 0 4px 0;font-size:14px;font-weight:600;'
                f'color:#111;line-height:1.6;">{linkify(content)}</p>'
            )

        # Indented notes
        elif stripped.startswith("↳"):
            note = stripped[1:].strip()
            # Bold the label before the colon
            note_html = re.sub(
                r'^([^:]+:)',
                r'<span style="color:#666;font-weight:600;">\1</span>',
                linkify(note)
            )
            html_parts.append(
                f'<p style="margin:0 0 14px 0;padding-left:16px;font-size:13px;'
                f'color:#555;line-height:1.6;border-left:2px solid #eee;">'
                f'{note_html}</p>'
            )

        # Special standalone sections
        elif stripped in ("SKILL OF THE WEEK", "REFLECTION PROMPT"):
            if in_context_block:
                html_parts.append('</p>')
                in_context_block = False
            icon = SPECIAL_ICONS.get(stripped, "")
            html_parts.append(
                f'<div style="background:#F9FAFB;border-radius:8px;padding:16px;'
                f'margin:28px 0 0 0;">'
                f'<span style="font-size:18px;margin-right:8px;vertical-align:middle;">{icon}</span>'
                f'<span style="font-size:11px;font-weight:700;color:#888;'
                f'text-transform:uppercase;letter-spacing:2px;vertical-align:middle;">{esc(stripped)}</span>'
            )
            html_parts.append('CLOSE_BOX')

        # Divider
        elif stripped == "---":
            if in_context_block:
                html_parts.append('</p>')
                in_context_block = False

        # Empty line
        elif stripped == "":
            if in_context_block:
                html_parts.append(' ')

        # Regular text (context prose or section content)
        else:
            if in_context_block:
                html_parts.append(esc(stripped) + ' ')
            else:
                # Check if previous part was CLOSE_BOX marker — this is the content
                if html_parts and html_parts[-1] == 'CLOSE_BOX':
                    html_parts[-1] = (
                        f'<p style="margin:8px 0 0 0;font-size:14px;color:#333;'
                        f'line-height:1.7;">{linkify(stripped)}</p></div>'
                    )
                else:
                    html_parts.append(
                        f'<p style="margin:0 0 8px 0;font-size:14px;color:#444;'
                        f'line-height:1.7;">{linkify(stripped)}</p>'
                    )

    if in_context_block:
        html_parts.append('</p>')

    # Close any unclosed box
    html_parts = [
        f'<p style="margin:8px 0 0 0;font-size:14px;color:#333;">—</p></div>'
        if p == 'CLOSE_BOX' else p
        for p in html_parts
    ]

    body_content = "\n".join(html_parts)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#F3F4F6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#F3F4F6;padding:32px 16px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

        <!-- Draft banner -->
        <tr><td style="background:#FEF3C7;border:1px solid #F59E0B;border-radius:6px;
                        padding:10px 16px;margin-bottom:16px;font-size:12px;
                        color:#92400E;text-align:center;font-weight:600;">
          DRAFT — Review and edit before forwarding to your team.
        </td></tr>

        <tr><td height="16"></td></tr>

        <!-- Main card -->
        <tr><td style="background:#ffffff;border-radius:12px;padding:36px 40px;
                        box-shadow:0 1px 3px rgba(0,0,0,0.08);">
          {body_content}
        </td></tr>

        <!-- Footer -->
        <tr><td style="padding:20px 0;text-align:center;font-size:11px;color:#9CA3AF;">
          Generated: {timestamp} &nbsp;·&nbsp; mosaic_pulse.py
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def send_email(subject: str, plain_body: str, html_body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = TO_EMAIL
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, [TO_EMAIL], msg.as_string())

    log.info(f"Email sent to {TO_EMAIL}")


def save_to_archive(html: str) -> None:
    """Commit the newsletter HTML to archive/ in the GitHub repo."""
    import github_store
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = f"archive/{date_str}.html"
    # Use a unique path if one already exists for today
    counter = 2
    existing = [name for name, _ in github_store.list_directory("archive")]
    base = f"{date_str}.html"
    while path.split("/")[-1] in existing:
        path = f"archive/{date_str}-{counter}.html"
        counter += 1
    github_store.write_file(path, html, f"Add newsletter archive {date_str}")
    log.info(f"Newsletter archived to {path} in GitHub")


def build_email_body(digest: str) -> tuple:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    plain = (
        "DRAFT — Review and edit before forwarding to your team.\n"
        + "-" * 60 + "\n\n"
        + digest
        + f"\n\n{'─' * 60}\nGenerated: {timestamp} | mosaic_pulse.py"
    )
    html = digest_to_html(digest, timestamp)
    save_to_archive(html)
    return plain, html


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    log.info("=== MosAIc Pulse starting ===")
    today_str = datetime.now().strftime("%d %B %Y")
    subject = f"[PULSE DRAFT] MosAIc Pulse — {today_str}"

    # Step 1: Collect RSS feeds
    raw_items = collect_all()

    # Step 1b: Collect from Gmail inbox
    gmail_items = collect_from_gmail()
    if gmail_items:
        raw_items.extend(gmail_items)
        log.info(f"Total items after Gmail merge: {len(raw_items)}")

    # Step 2: Filter (excluding URLs already featured in past issues)
    used_urls = load_used_urls()
    log.info(f"Loaded {len(used_urls)} previously featured URLs to exclude")
    editorial_items, youtube_items, job_items = filter_items(raw_items, used_urls=used_urls)

    if not editorial_items and not youtube_items and not job_items:
        log.warning("No items passed date filter — sending 'nothing this week' email")
        plain, html = build_email_body(
            f"MOSAIC PULSE — Week of {today_str}\n\n"
            "No new articles found in the last 30 days from monitored sources. "
            "Check feed health or expand the date window."
        )
        send_email(subject, plain, html)
        return

    # Step 3a: Haiku scoring for editorial
    try:
        scored_editorial = score_with_haiku(editorial_items)
    except Exception as e:
        log.error(f"Haiku editorial scoring failed: {e}")
        scored_editorial = editorial_items[:MAX_EDITORIAL_ITEMS]
        for item in scored_editorial:
            item["stream"] = "craft"

    # Step 3b: Haiku scoring for YouTube (separate, always picks one)
    scored_youtube = score_youtube_with_haiku(youtube_items)
    if not scored_youtube:
        log.info("No YouTube items available this week")

    # Mark job items
    for item in job_items:
        item["stream"] = "jobs"

    # Step 4: Sonnet synthesis
    try:
        digest = build_digest_with_sonnet(scored_editorial, scored_youtube, job_items)
    except Exception as e:
        log.error(f"Sonnet synthesis failed: {e}")
        # Fallback: send raw article list
        fallback_lines = [f"• {i['source_name']} — {i['title']}\n  {i['url']}" for i in scored_editorial + scored_youtube]
        plain, html = build_email_body(
            f"MOSAIC PULSE — Week of {today_str}\n\n"
            f"ERROR: Sonnet synthesis failed ({e}). Raw article list below:\n\n"
            + "\n".join(fallback_lines)
        )
        send_email(f"[PULSE ERROR] {subject}", plain, html)
        return

    # Step 5: Email
    plain, html = build_email_body(digest)
    send_email(subject, plain, html)

    # Record featured URLs so they're excluded from future issues
    featured_urls = [normalise_url(i["url"]) for i in scored_editorial + scored_youtube + job_items[:MAX_JOB_ITEMS]]
    save_used_urls(featured_urls, used_urls)
    log.info(f"Saved {len(featured_urls)} featured URLs to archive exclusion list")
    log.info("=== MosAIc Pulse complete ===")


if __name__ == "__main__":
    main()
