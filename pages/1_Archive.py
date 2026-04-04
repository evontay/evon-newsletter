"""
Archive page — browse all past MosAIc Pulse newsletters with podcast player.
"""

import re
import os
import json
import base64
import shutil
import subprocess
import sys
from pathlib import Path
import time

import streamlit as st
import streamlit.components.v1 as components
import github_store
from dotenv import load_dotenv

load_dotenv()

SCRIPT_DIR = Path(__file__).parent.parent
ARCHIVE_DIR = SCRIPT_DIR / "archive"
PYTHON312 = next(
    (p for p in ["/opt/homebrew/bin/python3.12", "/usr/local/bin/python3.12", shutil.which("python3.12") or ""]
     if p and Path(p).exists()),
    None,
)

st.set_page_config(page_title="MosAIc Pulse — Archive", page_icon="🗂️", layout="wide")

col_title, col_btn = st.columns([5, 1])
with col_title:
    st.title("🗂️ MosAIc Pulse — Archive")
with col_btn:
    st.write("")
    run_clicked = st.button("＋ Add Pulse", type="primary", use_container_width=True)

if run_clicked:
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
        st.cache_data.clear()
        st.rerun()
    else:
        st.error("Pulse run failed.")
        st.code(result.stderr or result.stdout, language="text")


# ── Data loading ───────────────────────────────────────────────────────────────

def extract_meta(html: str) -> dict:
    date_match = re.search(r'Week of ([^<]+)', html)
    date_label = date_match.group(1).strip() if date_match else "Unknown date"
    theme_match = re.search(
        r"This Week's Theme.*?<p[^>]*>(.*?)</p>",
        html, re.DOTALL | re.IGNORECASE
    )
    theme = re.sub(r'<[^>]+>', '', theme_match.group(1)).strip() if theme_match else ""
    return {"date_label": date_label, "theme": theme}


@st.cache_data(ttl=60)
def load_archive():
    files = github_store.list_directory("archive")
    html_files = {name: content for name, content in files if name.endswith(".html")}
    script_files = {name for name, _ in files if name.endswith("_script.json")}
    mp3_files = {name for name, _ in files if name.endswith(".mp3")}

    entries = []
    for filename in sorted(html_files, reverse=True):
        html = html_files[filename]
        stem = filename.replace(".html", "")
        meta = extract_meta(html)
        entries.append({
            "filename": filename,
            "stem": stem,
            "html": html,
            "has_podcast": f"{stem}_script.json" in script_files and f"{stem}.mp3" in mp3_files,
            **meta,
        })
    return entries


def load_podcast(stem: str):
    """Return (mp3_bytes, turns) for a given stem, checking local disk first."""
    mp3_local = ARCHIVE_DIR / f"{stem}.mp3"
    script_local = ARCHIVE_DIR / f"{stem}_script.json"

    if mp3_local.exists() and script_local.exists():
        return mp3_local.read_bytes(), json.loads(script_local.read_text())

    # Fall back to GitHub
    mp3_bytes = github_store.read_file_bytes(f"archive/{stem}.mp3")
    script_files = github_store.list_directory("archive")
    script_content = next((c for n, c in script_files if n == f"{stem}_script.json"), None)
    if mp3_bytes and script_content:
        return mp3_bytes, json.loads(script_content)
    return None, None


# ── Audio player component ─────────────────────────────────────────────────────

def render_player(mp3_bytes: bytes, turns: list[dict]):
    mp3_b64 = base64.b64encode(mp3_bytes).decode("utf-8")
    turns_json = json.dumps(turns)

    transcript_html = ""
    for i, turn in enumerate(turns):
        speaker = turn["speaker"]
        cls = "vera" if speaker == "VERA" else "kai"
        line = turn["line"].replace("<", "&lt;").replace(">", "&gt;")
        transcript_html += f"""
        <div class="turn" id="turn-{i}" data-start="{turn.get('start', 0)}" data-end="{turn.get('end', 9999)}">
          <div class="speaker {cls}">{speaker.title()}</div>
          <div class="line">{line}</div>
        </div>"""

    html = f"""<!DOCTYPE html><html><head><style>
      * {{ box-sizing: border-box; margin: 0; padding: 0; }}
      body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 12px; background: #fff; }}
      audio {{ width: 100%; margin-bottom: 10px; }}
      .controls {{ display: flex; align-items: center; gap: 6px; margin-bottom: 14px; }}
      .controls label {{ font-size: 12px; color: #888; margin-right: 2px; }}
      .speed-btn {{
        padding: 4px 10px; border: 1px solid #ddd; border-radius: 20px;
        cursor: pointer; font-size: 12px; background: #fff; color: #555;
        transition: all 0.15s;
      }}
      .speed-btn:hover {{ border-color: #4A6CF7; color: #4A6CF7; }}
      .speed-btn.active {{ background: #4A6CF7; color: #fff; border-color: #4A6CF7; font-weight: 600; }}
      .transcript {{ max-height: 420px; overflow-y: auto; border: 1px solid #f0f0f0; border-radius: 8px; padding: 8px; }}
      .turn {{
        padding: 8px 12px; margin-bottom: 4px; border-radius: 6px;
        opacity: 0.45; transition: opacity 0.25s, background 0.25s;
        cursor: pointer;
      }}
      .turn:hover {{ opacity: 0.75; }}
      .turn.active {{ opacity: 1; background: #F0F4FF; }}
      .speaker {{ font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.2px; margin-bottom: 3px; }}
      .speaker.vera {{ color: #7C3AED; }}
      .speaker.kai {{ color: #059669; }}
      .line {{ font-size: 13px; line-height: 1.65; color: #333; }}
    </style></head><body>
      <audio id="player" controls>
        <source src="data:audio/mpeg;base64,{mp3_b64}" type="audio/mpeg">
      </audio>
      <div class="controls">
        <label>Speed:</label>
        <button class="speed-btn" onclick="setSpeed(0.75,this)">0.75×</button>
        <button class="speed-btn active" onclick="setSpeed(1,this)">1×</button>
        <button class="speed-btn" onclick="setSpeed(1.25,this)">1.25×</button>
        <button class="speed-btn" onclick="setSpeed(1.5,this)">1.5×</button>
        <button class="speed-btn" onclick="setSpeed(2,this)">2×</button>
      </div>
      <div class="transcript" id="transcript">{transcript_html}</div>
      <script>
        const player = document.getElementById('player');
        const turns = {turns_json};

        function setSpeed(s, btn) {{
          player.playbackRate = s;
          document.querySelectorAll('.speed-btn').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
        }}

        // Click a transcript line to jump to that time
        document.querySelectorAll('.turn').forEach((el, i) => {{
          el.addEventListener('click', () => {{
            player.currentTime = turns[i].start || 0;
            player.play();
          }});
        }});

        player.addEventListener('timeupdate', () => {{
          const t = player.currentTime;
          turns.forEach((turn, i) => {{
            const el = document.getElementById('turn-' + i);
            if (!el) return;
            const active = t >= (turn.start || 0) && t < (turn.end || 9999);
            if (active && !el.classList.contains('active')) {{
              el.classList.add('active');
              el.scrollIntoView({{ block: 'nearest', behavior: 'smooth' }});
            }} else if (!active) {{
              el.classList.remove('active');
            }}
          }});
        }});
      </script>
    </body></html>"""

    components.html(html, height=620, scrolling=False)


# ── Main ───────────────────────────────────────────────────────────────────────

entries = load_archive()

if not entries:
    st.info("No newsletters archived yet. Click **＋ Add Pulse** to generate one.")
    st.stop()

st.caption(f"{len(entries)} newsletter{'s' if len(entries) != 1 else ''} in archive")
st.divider()

for entry in entries:
    label = f"**{entry['date_label']}**" + (f"  —  {entry['theme']}" if entry["theme"] else "")
    with st.expander(label):
        # Podcast section
        if entry["has_podcast"]:
            with st.spinner("Loading audio…"):
                mp3_bytes, turns = load_podcast(entry["stem"])
            if mp3_bytes and turns:
                render_player(mp3_bytes, turns)
                st.divider()
        else:
            if PYTHON312:
                if st.button("🎙️ Generate Podcast", key=f"pod_{entry['stem']}"):
                    env = os.environ.copy()
                    try:
                        env["GITHUB_TOKEN"] = st.secrets.get("GITHUB_TOKEN", env.get("GITHUB_TOKEN", ""))
                        env["GITHUB_REPO"] = st.secrets.get("GITHUB_REPO", env.get("GITHUB_REPO", "evontay/evon-newsletter"))
                        env["GITHUB_BRANCH"] = st.secrets.get("GITHUB_BRANCH", env.get("GITHUB_BRANCH", "master"))
                    except Exception:
                        pass

                    status = st.empty()
                    progress = st.progress(0)
                    status.markdown("✍️ Writing script…")

                    proc = subprocess.Popen(
                        [PYTHON312, str(SCRIPT_DIR / "podcast.py"),
                         str(ARCHIVE_DIR / entry["filename"])],
                        cwd=SCRIPT_DIR,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        env=env,
                    )

                    total_turns = None
                    for line in proc.stdout:
                        line = line.strip()
                        m_turns = re.search(r'Script generated — (\d+) turns', line)
                        m_synth = re.search(r'\[(\d+)/(\d+)\].*?(VERA|KAI):\s*(.{0,50})', line)
                        if m_turns:
                            total_turns = int(m_turns.group(1))
                            status.markdown("🎙️ Synthesising audio…")
                            progress.progress(0.15)
                        elif "Loading Kokoro" in line:
                            status.markdown("🔊 Loading voice model…")
                            progress.progress(0.18)
                        elif m_synth:
                            cur, total = int(m_synth.group(1)), int(m_synth.group(2))
                            speaker = m_synth.group(3).title()
                            snippet = m_synth.group(4).strip()
                            pct = 0.20 + (cur / total) * 0.70
                            progress.progress(pct)
                            status.markdown(f"🎙️ **{speaker}** ({cur}/{total}): *{snippet}…*")
                        elif "Audio saved" in line:
                            progress.progress(0.95)
                            status.markdown("💾 Saving files…")
                        elif "committed to GitHub" in line:
                            progress.progress(0.98)
                            status.markdown("☁️ Uploading to GitHub…")

                    proc.wait()
                    if proc.returncode == 0:
                        progress.progress(1.0)
                        status.markdown("✅ Podcast ready!")
                        time.sleep(0.8)
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        status.empty()
                        progress.empty()
                        st.error("Podcast generation failed — check terminal for details.")
            else:
                st.caption("Podcast generation requires Python 3.12 + Kokoro (local only).")

        # Newsletter HTML
        components.html(entry["html"], height=900, scrolling=True)
