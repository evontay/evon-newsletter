#!/usr/bin/env python3.12
"""
MosAIc Pulse — Podcast Generator
Turns a newsletter digest into a Vera, Kai, Dan & Carla audio episode.

Usage:
    python3.12 podcast.py                        # uses latest archive
    python3.12 podcast.py archive/2026-04-04.html
"""

import os
import re
import sys
import json
import tempfile
import logging
from pathlib import Path

import numpy as np
import soundfile as sf
from dotenv import load_dotenv
import anthropic

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
SONNET_MODEL = "claude-sonnet-4-20250514"

VOICES = {
    "VERA":  "af_nova",      # Big-picture strategist
    "KAI":   "am_echo",      # Design practitioner
    "DAN":   "am_puck",      # DesignOps specialist
    "CARLA": "af_jessica",   # User researcher
}
SAMPLE_RATE = 24000

# Short silence between turns (0.4s)
PAUSE = np.zeros(int(SAMPLE_RATE * 0.4), dtype=np.float32)
# Longer pause between sections (0.8s)
SECTION_PAUSE = np.zeros(int(SAMPLE_RATE * 0.8), dtype=np.float32)


# ── Step 1: Read newsletter ────────────────────────────────────────────────────

def load_latest_archive() -> tuple[str, str]:
    """Return (filename, plain_text) of the most recent archived newsletter."""
    archive_dir = Path(__file__).parent / "archive"
    html_files = sorted(archive_dir.glob("*.html"), reverse=True)
    if not html_files:
        raise FileNotFoundError("No archived newsletters found in archive/")
    path = html_files[0]
    html = path.read_text(encoding="utf-8")
    return path.name, html_to_text(html)


def html_to_text(html: str) -> str:
    """Strip HTML tags and decode basic entities."""
    text = re.sub(r'<[^>]+>', ' ', html)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ── Step 2: Generate script ────────────────────────────────────────────────────

BASE_PROMPT = """You are writing a podcast script for an internal UX/DesignOps team at MosAIc AI Experience, Singapore — a team in the middle of an AI transformation.

The four hosts are AI personas with distinct roles and voices:
- VERA: Big-picture strategist. Thinks in systems, long arcs, and structural shifts. Raises implications and tensions others haven't seen yet. Can be a little intense about the future.
- KAI: Design practitioner. Focused on craft — interaction design, design systems, visual and UX quality. Translates abstract ideas into what they concretely mean for day-to-day design work. Grounded, occasionally dry wit.
- DAN: DesignOps specialist. Thinks in workflows, tooling, team rituals, and organisational friction. Pragmatic. Spots the process bottleneck everyone else misses. Sceptical of things that sound good in theory but break in practice.
- CARLA: User researcher. Centres the human perspective — who gets left out, what the data doesn't capture, what assumptions are being made. Brings empathy and rigour. Pushes back when the team moves too fast.

Line-level rules (apply to every turn):
- Each line is 3-6 sentences. Substantive — develop an argument, share a specific example, name a tension. Not just reactions.
- Natural spoken rhythm. Contractions, the occasional incomplete thought. Warmth and banter.
- No bullet points, no lists, no markdown, no stage directions.
- Output ONLY a valid JSON array: [{"speaker": "VERA", "line": "..."}, ...]"""

HOST_FRAMES = {
    "VERA": """
EPISODE FORMAT — VERA IS LEADING:
Vera scans this week's newsletter and picks the ONE story or signal that she finds most significant for the long-term trajectory of UX and AI — the thing she thinks the team is underestimating or hasn't fully sat with yet.

Structure:
1. Vera opens with 2-3 turns establishing why this topic matters strategically — not just this week, but over the next 2-3 years. She is specific and opinionated.
2. She invites Kai and Carla in to pressure-test her thinking. Kai should challenge from the craft angle (does this hold up when you're actually designing?), Carla from the human angle (who does this leave out?).
3. The conversation goes deep — disagreement, specific examples, moments where someone changes their mind or sharpens their view. Dan can appear briefly if the DesignOps angle becomes relevant.
4. After 25-30 turns of real depth, the group lands on a shared implication for the team — something concrete they'd actually do differently.
5. Total: 30-38 turns. Vera speaks roughly 40% of turns. Stay on ONE topic the entire episode.""",

    "KAI": """
EPISODE FORMAT — KAI IS LEADING:
Kai picks the ONE story from the newsletter most relevant to design craft and practice — a shift in how design work is actually done, evaluated, or taught.

Structure:
1. Kai opens with 2-3 turns working through what this means concretely for the team's day-to-day work — specific design decisions, quality standards, how they'd approach a real project differently.
2. He invites Dan and Vera in. Dan should probe the workflow and tooling implications (how do we actually implement this?), Vera the strategic framing (why does this matter beyond this sprint?).
3. The conversation stays in the concrete — examples from real projects, specific tools, actual design decisions. When it gets too abstract, Kai pulls it back.
4. Carla can appear if the user perspective becomes directly relevant. After 25-30 turns, land on a practical next step the team could take this week.
5. Total: 30-38 turns. Kai speaks roughly 40% of turns. Stay on ONE topic the entire episode.""",

    "DAN": """
EPISODE FORMAT — DAN IS LEADING:
Dan picks the ONE story most relevant to how the team actually operates — a workflow bottleneck, a tooling shift, a process that needs rethinking in light of AI.

Structure:
1. Dan opens with 2-3 turns identifying the specific operational problem or opportunity he sees. He is concrete — naming the friction point, the handoff that breaks, the ritual that no longer fits.
2. He invites Kai and Vera in. Kai should validate or challenge from the craft side (does this match what designers actually experience?), Vera from the strategic side (is this a symptom of something bigger?).
3. The conversation digs into the how — what would actually change, who would resist it, what you'd need to put in place. Carla appears if the researcher workflow or participant experience is at stake.
4. After 25-30 turns, land on a specific process change or experiment the team could try.
5. Total: 30-38 turns. Dan speaks roughly 40% of turns. Stay on ONE topic the entire episode.""",

    "CARLA": """
EPISODE FORMAT — CARLA IS LEADING:
Carla picks the ONE story where she thinks the human perspective is most at risk of being overlooked — a place where the team's AI enthusiasm might be running ahead of the evidence.

Structure:
1. Carla opens with 2-3 turns naming what she's worried about — which users, which assumptions, which research gaps. She is specific and grounded in evidence or its absence.
2. She invites Dan and Vera in. Dan should engage with the operational side (what would it actually take to slow down and do this properly?), Vera with the systemic angle (is this a pattern across the industry, not just us?).
3. The conversation grapples honestly with the tension between moving fast and being rigorous. Kai can appear when the craft implications become direct.
4. After 25-30 turns, land on a concrete research or validation step the team should take before moving forward.
5. Total: 30-38 turns. Carla speaks roughly 40% of turns. Stay on ONE topic the entire episode.""",
}


def generate_script(digest_text: str, lead_host: str = "VERA") -> list[dict]:
    """Call Claude Sonnet to generate a host-led podcast episode."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    host_frame = HOST_FRAMES.get(lead_host.upper(), HOST_FRAMES["VERA"])
    system = BASE_PROMPT + "\n" + host_frame

    log.info(f"Generating podcast script — lead host: {lead_host}")
    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=8000,
        system=system,
        messages=[{
            "role": "user",
            "content": f"Here is this week's newsletter digest. Write the episode led by {lead_host.title()}.\n\n{digest_text}"
        }],
    )

    raw = response.content[0].text.strip()
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if not match:
        raise ValueError("Claude did not return a valid JSON array")
    turns = json.loads(match.group())
    log.info(f"Script generated — {len(turns)} turns")
    return turns


# ── Step 3: Text-to-speech ─────────────────────────────────────────────────────

def synthesise(turns: list[dict]) -> np.ndarray:
    """Convert script turns to a single audio array using Kokoro.
    Adds start/end timestamps (in seconds) to each turn dict in place."""
    from kokoro_onnx import Kokoro

    log.info("Loading Kokoro TTS model…")
    kokoro = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")

    audio_chunks = []
    cumulative = 0  # samples so far
    for i, turn in enumerate(turns):
        speaker = turn["speaker"]
        line = turn["line"]
        voice = VOICES.get(speaker, VOICES["KAI"])

        log.info(f"  [{i+1}/{len(turns)}] {speaker}: {line[:60]}…")
        samples, _ = kokoro.create(line, voice=voice, speed=1.0, lang="en-us")

        turn["start"] = round(cumulative / SAMPLE_RATE, 2)
        turn["end"] = round((cumulative + len(samples)) / SAMPLE_RATE, 2)
        cumulative += len(samples) + len(PAUSE)

        audio_chunks.append(samples)
        audio_chunks.append(PAUSE)

    return np.concatenate(audio_chunks)


# ── Step 4: Save outputs ───────────────────────────────────────────────────────

def save_outputs(turns: list[dict], audio: np.ndarray, source_filename: str, lead_host: str = "VERA"):
    """Save the script as JSON and the audio as MP3."""
    archive_dir = Path(__file__).parent / "archive"
    date_stem = source_filename.replace(".html", "")
    stem = f"{date_stem}_{lead_host.lower()}"

    # Save script
    script_path = archive_dir / f"{stem}_script.json"
    script_content = json.dumps(turns, indent=2, ensure_ascii=False)
    script_path.write_text(script_content, encoding="utf-8")
    log.info(f"Script saved to {script_path}")

    # Save audio as WAV first, then convert to MP3
    wav_path = archive_dir / f"{stem}.wav"
    mp3_path = archive_dir / f"{stem}.mp3"

    sf.write(str(wav_path), audio, SAMPLE_RATE)

    # Convert to MP3 using pydub + ffmpeg
    try:
        from pydub import AudioSegment
        AudioSegment.from_wav(str(wav_path)).export(str(mp3_path), format="mp3", bitrate="128k")
        wav_path.unlink()
        log.info(f"Audio saved to {mp3_path}")
    except Exception as e:
        log.warning(f"MP3 conversion failed ({e}) — WAV file kept at {wav_path}")
        return

    # Commit both files to GitHub so they're available on Streamlit Cloud
    try:
        import github_store
        github_store.write_file(f"archive/{stem}_script.json", script_content, f"Add podcast script {stem}")
        log.info("Script committed to GitHub")
        github_store.write_file(f"archive/{stem}.mp3", mp3_path.read_bytes(), f"Add podcast audio {stem}")
        log.info("Podcast files committed to GitHub")
    except Exception as e:
        log.warning(f"Could not commit podcast to GitHub: {e} — files saved locally only")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("html_file", nargs="?", help="Path to newsletter HTML file")
    parser.add_argument("--host", default="VERA", choices=["VERA", "KAI", "DAN", "CARLA"],
                        help="Which host leads the episode")
    args = parser.parse_args()

    if args.html_file:
        path = Path(args.html_file)
        html = path.read_text(encoding="utf-8")
        filename = path.name
        digest_text = html_to_text(html)
    else:
        filename, digest_text = load_latest_archive()

    log.info(f"Generating podcast for: {filename} — lead host: {args.host}")

    model_path = Path(__file__).parent / "kokoro-v1.0.onnx"
    voices_path = Path(__file__).parent / "voices-v1.0.bin"
    if not model_path.exists() or not voices_path.exists():
        log.error(
            "Kokoro model files not found. Download them:\n"
            "  curl -L -o kokoro-v1.0.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx\n"
            "  curl -L -o voices-v1.0.bin https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
        )
        sys.exit(1)

    turns = generate_script(digest_text, lead_host=args.host)
    audio = synthesise(turns)
    save_outputs(turns, audio, filename, lead_host=args.host)
    log.info("=== Podcast complete ===")


if __name__ == "__main__":
    main()
