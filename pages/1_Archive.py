"""
Archive page — browse all past MosAIc Pulse newsletters.
"""

import re
import os
import subprocess
import sys
import streamlit as st
import streamlit.components.v1 as components
import github_store
from dotenv import load_dotenv

load_dotenv()

SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

st.set_page_config(page_title="MosAIc Pulse — Archive", page_icon="🗂️", layout="wide")

col_title, col_btn = st.columns([5, 1])
with col_title:
    st.title("🗂️ MosAIc Pulse — Archive")
with col_btn:
    st.write("")  # vertical alignment nudge
    run_clicked = st.button("＋ Add Pulse", type="primary", use_container_width=True)

if run_clicked:
    # Pass GitHub credentials to the subprocess so it can commit the archive
    env = os.environ.copy()
    try:
        env["GITHUB_TOKEN"] = st.secrets.get("GITHUB_TOKEN", env.get("GITHUB_TOKEN", ""))
        env["GITHUB_REPO"] = st.secrets.get("GITHUB_REPO", env.get("GITHUB_REPO", "evontay/evon-newsletter"))
        env["GITHUB_BRANCH"] = st.secrets.get("GITHUB_BRANCH", env.get("GITHUB_BRANCH", "master"))
    except Exception:
        pass
    with st.spinner("Running MosAIc Pulse — this takes a minute…"):
        result = subprocess.run(
            [sys.executable, "mosaic_pulse.py"],
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
            env=env,
        )
    if result.returncode == 0:
        st.success("Pulse generated and archived.")
        st.rerun()
    else:
        st.error("Pulse run failed.")
        st.code(result.stderr or result.stdout, language="text")


def extract_meta(html: str) -> dict:
    """Pull date label and theme out of a saved newsletter HTML file."""
    # Date: "Week of DD Month YYYY" from the subtitle paragraph
    date_match = re.search(r'Week of ([^<]+)', html)
    date_label = date_match.group(1).strip() if date_match else "Unknown date"

    # Theme: paragraph immediately after "This Week's Theme" span
    theme_match = re.search(
        r"This Week's Theme.*?<p[^>]*>(.*?)</p>",
        html, re.DOTALL | re.IGNORECASE
    )
    theme = re.sub(r'<[^>]+>', '', theme_match.group(1)).strip() if theme_match else ""

    return {"date_label": date_label, "theme": theme}


@st.cache_data(ttl=60)
def load_archive():
    files = github_store.list_directory("archive")
    files = [(name, html) for name, html in files if name.endswith(".html")]
    files.sort(key=lambda x: x[0], reverse=True)
    entries = []
    for filename, html in files:
        meta = extract_meta(html)
        entries.append({"filename": filename, "html": html, **meta})
    return entries


entries = load_archive()

if not entries:
    st.info("No newsletters archived yet. Click **＋ Add Pulse** to generate one.")
    st.stop()

st.caption(f"{len(entries)} newsletter{'s' if len(entries) != 1 else ''} in archive")
st.divider()

for entry in entries:
    with st.expander(f"**{entry['date_label']}**" + (f"  —  {entry['theme']}" if entry["theme"] else "")):
        components.html(entry["html"], height=900, scrolling=True)
