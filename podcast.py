#!/usr/bin/env python3.12
"""
MosAIc Pulse — Podcast Generator
Turns a newsletter digest into a Vera & Kai audio episode.

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

# Kokoro voice IDs — af_heart is warm/female, am_michael is grounded/male
VERA_VOICE = "af_nova"   # Vera: big-picture strategist
KAI_VOICE  = "am_echo"  # Kai: on-the-ground practitioner
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

SYSTEM_PROMPT = """You are writing a podcast script for an internal UX/DesignOps team at MosAIc AI Experience, Singapore.

The two hosts are AI personas:
- VERA: Big-picture strategist. Thinks in systems, trends, long arcs. Raises implications and tensions. Can be a little intense about the future.
- KAI: On-the-ground practitioner. Translates ideas into day-to-day design work. Sceptical of hype, grounded, occasionally dry wit.

Their dynamic: bantery, warm, mutually respectful, genuinely curious. They push back on each other to arrive at joint insights. Neither wins — they build together.

Format rules:
- Output ONLY a JSON array of turns: [{"speaker": "VERA", "line": "..."}, ...]
- 30-40 turns total (~2000-2500 words across all lines)
- Open with a brief informal exchange (2-3 turns) before diving in
- Cover the major stories from the newsletter — don't just summarise, debate the implications
- Each line should be 1-4 sentences. Natural spoken rhythm. No bullet points.
- End with both hosts landing on a shared takeaway for the team
- No section headers, no stage directions, no markdown — just the JSON array"""


def generate_script(digest_text: str) -> list[dict]:
    """Call Claude Sonnet to generate the Vera & Kai dialogue."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    log.info("Generating podcast script with Claude Sonnet…")
    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"Here is this week's newsletter digest. Write the Vera & Kai episode.\n\n{digest_text}"
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
        voice = VERA_VOICE if speaker == "VERA" else KAI_VOICE

        log.info(f"  [{i+1}/{len(turns)}] {speaker}: {line[:60]}…")
        samples, _ = kokoro.create(line, voice=voice, speed=1.0, lang="en-us")

        turn["start"] = round(cumulative / SAMPLE_RATE, 2)
        turn["end"] = round((cumulative + len(samples)) / SAMPLE_RATE, 2)
        cumulative += len(samples) + len(PAUSE)

        audio_chunks.append(samples)
        audio_chunks.append(PAUSE)

    return np.concatenate(audio_chunks)


# ── Step 4: Save outputs ───────────────────────────────────────────────────────

def save_outputs(turns: list[dict], audio: np.ndarray, source_filename: str):
    """Save the script as JSON and the audio as MP3."""
    archive_dir = Path(__file__).parent / "archive"
    stem = source_filename.replace(".html", "")

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
        github_store.write_file(f"archive/{stem}.mp3", mp3_path.read_bytes(), f"Add podcast audio {stem}")
        log.info("Podcast files committed to GitHub")
    except Exception as e:
        log.warning(f"Could not commit podcast to GitHub: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        html = path.read_text(encoding="utf-8")
        filename = path.name
        digest_text = html_to_text(html)
    else:
        filename, digest_text = load_latest_archive()

    log.info(f"Generating podcast for: {filename}")

    # Check Kokoro model files exist
    model_path = Path(__file__).parent / "kokoro-v1.0.onnx"
    voices_path = Path(__file__).parent / "voices-v1.0.bin"
    if not model_path.exists() or not voices_path.exists():
        log.error(
            "Kokoro model files not found. Download them:\n"
            "  curl -L -o kokoro-v1.0.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx\n"
            "  curl -L -o voices-v1.0.bin https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
        )
        sys.exit(1)

    turns = generate_script(digest_text)
    audio = synthesise(turns)
    save_outputs(turns, audio, filename)
    log.info("=== Podcast complete ===")


if __name__ == "__main__":
    main()
