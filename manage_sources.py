"""
Streamlit UI for managing newsletter sources in sources.py.
Run with: streamlit run manage_sources.py
"""

import re
import requests
import streamlit as st
import feedparser
from urllib.parse import urljoin
from sources import SOURCES

SOURCES_FILE = "sources.py"

CATEGORIES = [
    "Craft & Practice",
    "Tools & AI Fluency",
    "Design Leadership",
    "Horizon Watch",
    "YouTube",
    "Jobs / Skills Market",
]

# Map comment strings in sources.py to category labels
COMMENT_TO_CATEGORY = {
    "Craft & Practice": "Craft & Practice",
    "Tools & AI Fluency": "Tools & AI Fluency",
    "Design Leadership": "Design Leadership",
    "Horizon Watch": "Horizon Watch",
    "YouTube": "YouTube",
    "Jobs / Skills Market": "Jobs / Skills Market",
}


def parse_sources_with_categories():
    """Parse sources.py and return list of (category, {name, url}) tuples."""
    with open(SOURCES_FILE) as f:
        content = f.read()

    result = []
    current_category = "Uncategorized"
    for line in content.splitlines():
        stripped = line.strip()
        # Comment line => new category
        if stripped.startswith("#"):
            cat = stripped.lstrip("# ").strip()
            if cat in COMMENT_TO_CATEGORY:
                current_category = COMMENT_TO_CATEGORY[cat]
        # Source line
        elif stripped.startswith('{"url"'):
            url_match = re.search(r'"url":\s*"([^"]+)"', stripped)
            name_match = re.search(r'"name":\s*"([^"]+)"', stripped)
            if url_match and name_match:
                result.append({
                    "category": current_category,
                    "name": name_match.group(1),
                    "url": url_match.group(1),
                })
    return result


def write_sources(sources_by_category):
    """Write sources back to sources.py, preserving category groupings."""
    lines = ["SOURCES = [\n"]
    for i, category in enumerate(CATEGORIES):
        items = sources_by_category.get(category, [])
        if not items:
            continue
        if i > 0:
            lines.append("\n")
        lines.append(f"    # {category}\n")
        for item in items:
            lines.append(f'    {{"url": "{item["url"]}", "name": "{item["name"]}"}},\n')
    lines.append("]\n")
    with open(SOURCES_FILE, "w") as f:
        f.writelines(lines)


HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; feed-discovery/1.0)"}


def _youtube_feed(url):
    """Extract RSS feed URL and title from a YouTube channel URL."""
    resp = requests.get(url, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    match = re.search(r'"externalId":"([^"]+)"', resp.text)
    if not match:
        raise ValueError("Could not find YouTube channel ID on that page.")
    channel_id = match.group(1)
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed = feedparser.parse(feed_url)
    return feed_url, feed.feed.get("title", "")


def _find_feed_link(html, base_url):
    """Look for <link rel=alternate type=rss/atom> in HTML."""
    # href before type
    m = re.search(
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]*type=["\']application/(?:rss|atom)\+xml["\']',
        html, re.IGNORECASE,
    )
    if m:
        return urljoin(base_url, m.group(1))
    # type before href
    m = re.search(
        r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*href=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if m:
        return urljoin(base_url, m.group(1))
    return None


def discover_feed(raw_url):
    """Given any URL (channel page, blog, or direct feed), return (feed_url, title).
    Raises ValueError with a human-readable message if nothing is found."""
    # YouTube
    if "youtube.com" in raw_url or "youtu.be" in raw_url:
        return _youtube_feed(raw_url)

    # Try as a direct feed first
    feed = feedparser.parse(raw_url)
    if feed.entries:
        return raw_url, feed.feed.get("title", "")

    # Fetch page HTML and look for feed links
    try:
        resp = requests.get(raw_url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        raise ValueError(f"Could not fetch URL: {e}")

    feed_url = _find_feed_link(resp.text, raw_url)
    if feed_url:
        feed = feedparser.parse(feed_url)
        if feed.entries:
            return feed_url, feed.feed.get("title", "")

    # Try common feed paths as a fallback
    for path in ["/feed", "/rss", "/feed.xml", "/atom.xml", "/rss.xml", "/index.xml"]:
        candidate = urljoin(raw_url, path)
        f = feedparser.parse(candidate)
        if f.entries:
            return candidate, f.feed.get("title", "")

    raise ValueError("Could not find an RSS/Atom feed for this URL.")


def validate_feed(url):
    """Try to fetch and parse the RSS feed. Returns (ok, message)."""
    try:
        feed = feedparser.parse(url)
        if not feed.entries:
            if feed.bozo:
                return False, f"Feed parse error: {feed.bozo_exception}"
            return False, "Feed loaded but has no entries — double-check the URL."
        title = feed.feed.get("title", "untitled")
        return True, f'Feed OK — "{title}" ({len(feed.entries)} entries found)'
    except Exception as e:
        return False, str(e)


# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="MosAIc Pulse — Sources", page_icon="📡", layout="wide")
st.title("📡 MosAIc Pulse — Source Manager")
st.caption(f"Managing `{SOURCES_FILE}` · {len(SOURCES)} sources total")

# Load sources grouped by category
all_sources = parse_sources_with_categories()
by_category = {cat: [] for cat in CATEGORIES}
for s in all_sources:
    by_category.setdefault(s["category"], []).append(s)

# ── Add new source ─────────────────────────────────────────────────────────────
st.subheader("Add a new source")
st.caption("Paste any URL — a YouTube channel, blog homepage, or direct RSS feed. The app will find the feed automatically.")

with st.form("add_source", clear_on_submit=True):
    col1, col2, col3 = st.columns([3, 2, 1])
    with col1:
        raw_url = st.text_input("URL", placeholder="https://www.youtube.com/@LennysPodcast or https://example.com")
    with col2:
        new_name = st.text_input("Display name (optional — auto-filled from feed title)")
    with col3:
        new_cat = st.selectbox("Category", CATEGORIES)

    submitted = st.form_submit_button("Add source", type="primary")

    if submitted:
        if not raw_url:
            st.error("URL is required.")
        else:
            existing_urls = [s["url"] for s in all_sources]
            with st.spinner("Finding feed…"):
                try:
                    feed_url, feed_title = discover_feed(raw_url)
                except ValueError as e:
                    st.error(str(e))
                    feed_url = None

            if feed_url:
                if feed_url in existing_urls:
                    st.warning(f"This feed is already in sources.")
                else:
                    name = new_name.strip() or feed_title or raw_url
                    if feed_url != raw_url:
                        st.info(f"Feed found: `{feed_url}`")
                    by_category[new_cat].append({"name": name, "url": feed_url, "category": new_cat})
                    write_sources(by_category)
                    st.success(f"Added **{name}** to {new_cat}.")
                    st.rerun()

st.divider()

# ── Current sources ────────────────────────────────────────────────────────────
st.subheader("Current sources")

to_delete = None

for category in CATEGORIES:
    items = by_category.get(category, [])
    if not items:
        continue
    with st.expander(f"{category} ({len(items)})", expanded=True):
        for item in items:
            col_name, col_url, col_del = st.columns([2, 4, 0.6])
            with col_name:
                st.write(item["name"])
            with col_url:
                st.caption(item["url"])
            with col_del:
                if st.button("Remove", key=f"del_{item['url']}"):
                    to_delete = item

# Handle deletion outside the loop
if to_delete:
    by_category[to_delete["category"]] = [
        s for s in by_category[to_delete["category"]] if s["url"] != to_delete["url"]
    ]
    write_sources(by_category)
    st.success(f"Removed **{to_delete['name']}**.")
    st.rerun()
