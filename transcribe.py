#!/usr/bin/env python3
"""Batch-transcribe audio/video files using Handy's local Parakeet model.

Reuses the ONNX model files that the Handy dictation app already downloaded
(~/Library/Application Support/com.pais.handy/models/parakeet-tdt-0.6b-v3-int8),
so there is nothing extra to download and everything runs offline on-device.

For each input it writes, next to the source file:
  <name>.md   a readable transcript with small YAML frontmatter
  <name>.srt  timestamped subtitle cues

Usage:
  transcribe                       # process everything in ~/Transcribe-Inbox
  transcribe file1.m4a dir/        # files and/or folders
  transcribe --out DIR ...         # write outputs to DIR instead of beside source
  transcribe --force ...           # re-do files even if a .md already exists
"""
from __future__ import annotations

import argparse
import datetime as _dt
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np


def _find_ffmpeg() -> str:
    """Locate ffmpeg even when launched from Finder (no shell PATH)."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    for cand in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if Path(cand).exists():
            return cand
    return "ffmpeg"  # last resort; will raise a clear error if truly missing


FFMPEG = _find_ffmpeg()

MODEL_NAME = "nemo-parakeet-tdt-0.6b-v3"
DEFAULT_MODEL_DIR = (
    Path.home()
    / "Library/Application Support/com.pais.handy/models/parakeet-tdt-0.6b-v3-int8"
)
DROP_DIR = Path.home() / "Transcribe-Inbox"
SAMPLE_RATE = 16000

# Parakeet's encoder has a fixed positional-encoding limit and crashes outright
# once the input exceeds ~5000 frames (about 400 s of audio): onnxruntime raises
# "Attempting to broadcast an axis ... 77 by 5077" from a self-attention Add node.
# So anything longer must be transcribed in windows, with each window's
# timestamps offset back to absolute time.
#
# WINDOW_SEC is the target chunk length, kept comfortably under that hard ceiling.
# To avoid slicing a word in half at a window boundary (which loses or garbles a
# word per cut), we search back up to WINDOW_SLACK_SEC for the quietest spot and
# cut there instead. Worst-case chunk length is WINDOW_SEC, still well under the
# ceiling.
#
# The window is much shorter than the ceiling requires because, well before the
# crash, Parakeet silently omits stretches of speech as a single pass grows: a
# 121 s clip lost 27% of its words in one pass versus 45 s windows, and gains
# flatten below 45 s.
WINDOW_SEC = 45
WINDOW_SLACK_SEC = 10

# SRT cue sizing.
CUE_MAX_CHARS = 84
CUE_MAX_SEC = 6.0
SENTENCE_END = (".", "?", "!", "…")
import re as _re

_SPACE_BEFORE_PUNCT = _re.compile(r"\s+([,.;:!?…])")


def tidy(text: str) -> str:
    """Remove stray spaces the tokenizer leaves before punctuation."""
    return _SPACE_BEFORE_PUNCT.sub(r"\1", text).strip()

AUDIO_EXTS = {
    ".wav", ".mp3", ".m4a", ".m4b", ".aac", ".flac", ".ogg", ".oga",
    ".opus", ".wma", ".aiff", ".aif", ".aifc", ".caf",
    ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".3gp",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def resolve_model_dir(arg: str | None) -> Path:
    if arg:
        d = Path(arg).expanduser()
        if not d.is_dir():
            sys.exit(f"Model directory not found: {d}")
        return d
    if DEFAULT_MODEL_DIR.is_dir():
        return DEFAULT_MODEL_DIR
    # Fallback: Handy may use a slightly different folder suffix.
    base = DEFAULT_MODEL_DIR.parent
    if base.is_dir():
        for cand in sorted(base.glob("parakeet-tdt-0.6b-v3*")):
            if cand.is_dir():
                return cand
    sys.exit(
        "Could not find Handy's Parakeet model. Looked in:\n"
        f"  {DEFAULT_MODEL_DIR}\n"
        "Open Handy and make sure the parakeet-tdt-0.6b-v3 model is downloaded, "
        "or pass --model <dir>."
    )


def decode_to_wav(src: Path, dst: Path) -> None:
    """Decode any audio/video file to 16 kHz mono PCM16 WAV via ffmpeg."""
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(src),
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
        str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()[:300]}")


def read_wav_mono(path: Path) -> np.ndarray:
    """Read a 16-bit mono WAV into a float32 array in [-1, 1]."""
    with wave.open(str(path), "rb") as wf:
        n = wf.getnframes()
        raw = wf.readframes(n)
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return audio


def srt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    td = _dt.timedelta(seconds=seconds)
    total_ms = int(round(td.total_seconds() * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_cues(tokens, timestamps, audio_dur):
    """Group token-level timestamps into caption-sized SRT cues.

    Returns a list of (start, end, text) tuples.
    """
    cues = []
    cur_tokens: list[str] = []
    cur_start = None

    def flush(end_time):
        nonlocal cur_tokens, cur_start
        if not cur_tokens:
            return
        text = tidy("".join(cur_tokens))
        if text:
            # Punctuation-only fragments belong on the previous cue, not alone.
            if cues and not _re.search(r"\w", text):
                ps, _pe, pt = cues[-1]
                cues[-1] = (ps, end_time, tidy(pt + text))
            else:
                cues.append((cur_start, end_time, text))
        cur_tokens = []
        cur_start = None

    pending_break = False
    for i, (tok, ts) in enumerate(zip(tokens, timestamps)):
        # A token that begins a new word starts with a space (subword
        # continuations do not). Only break cues at word boundaries so we
        # never split a word like "blo" / "cks" across two cues.
        starts_word = (i == 0) or tok[:1].isspace() or tok[:1] == "▁"
        if pending_break and starts_word and cur_tokens:
            flush(ts)
            pending_break = False
        if cur_start is None:
            cur_start = ts
        cur_tokens.append(tok)
        next_ts = timestamps[i + 1] if i + 1 < len(timestamps) else audio_dur
        cur_text = "".join(cur_tokens).strip()
        if cur_text.endswith(SENTENCE_END):
            flush(next_ts)
            pending_break = False
        elif len(cur_text) >= CUE_MAX_CHARS or (next_ts - cur_start) >= CUE_MAX_SEC:
            pending_break = True  # break at the next word boundary
    flush(audio_dur)
    return cues


def split_points(audio: np.ndarray, sr: int = SAMPLE_RATE) -> list[tuple[int, int]]:
    """Return (start, end) sample ranges that tile `audio` into safe windows.

    Each range is at most WINDOW_SEC long (so the encoder never overflows). To
    avoid cutting through a word, the boundary is placed at the quietest 0.1 s
    frame within the last WINDOW_SLACK_SEC of each window; in natural speech that
    lands in a pause between words.
    """
    n = len(audio)
    win = WINDOW_SEC * sr
    slack = WINDOW_SLACK_SEC * sr
    frame = max(1, sr // 10)  # 0.1 s granularity for the silence search
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < n:
        ideal_end = start + win
        if ideal_end >= n:
            ranges.append((start, n))
            break
        lo = max(start + frame, ideal_end - slack)
        # RMS of each 0.1 s frame in [lo, ideal_end); cut at the quietest one.
        region = audio[lo:ideal_end]
        nf = len(region) // frame
        if nf >= 1:
            frames = region[: nf * frame].reshape(nf, frame)
            rms = np.sqrt((frames.astype(np.float32) ** 2).mean(axis=1))
            cut = lo + int(rms.argmin()) * frame + frame // 2
        else:
            cut = ideal_end
        ranges.append((start, cut))
        start = cut
    return ranges


def transcribe_file(model, wav_path: Path):
    """Return (full_text, cues) for a decoded WAV using token timestamps."""
    audio = read_wav_mono(wav_path)
    dur = len(audio) / SAMPLE_RATE
    tx = model.with_timestamps()

    if dur <= WINDOW_SEC:
        r = tx.recognize(str(wav_path))
        cues = build_cues(r.tokens, r.timestamps, dur)
        return r.text.strip(), cues

    # Long file: process in silence-aligned windows, offsetting timestamps to
    # absolute time so the merged SRT stays correct.
    all_text: list[str] = []
    all_cues = []
    for start, end in split_points(audio, SAMPLE_RATE):
        chunk = audio[start:end]
        offset = start / SAMPLE_RATE
        chunk_dur = len(chunk) / SAMPLE_RATE
        r = tx.recognize(chunk, sample_rate=SAMPLE_RATE)
        if r.text.strip():
            all_text.append(r.text.strip())
        abs_ts = [t + offset for t in r.timestamps]
        for s, e, txt in build_cues(r.tokens, abs_ts, offset + chunk_dur):
            all_cues.append((s, e, txt))
    return " ".join(all_text), all_cues


def write_srt(path: Path, cues) -> None:
    lines = []
    for idx, (start, end, text) in enumerate(cues, 1):
        lines.append(str(idx))
        lines.append(f"{srt_timestamp(start)} --> {srt_timestamp(end)}")
        lines.append(text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def paragraphs_from_cues(cues) -> str:
    """Join cue text into readable paragraphs, breaking on long pauses."""
    if not cues:
        return ""
    paras: list[list[str]] = [[]]
    prev_end = cues[0][0]
    for start, end, text in cues:
        if start - prev_end > 2.0 and paras[-1]:
            paras.append([])
        paras[-1].append(text)
        prev_end = end
    return "\n\n".join(tidy(" ".join(p)) for p in paras if p)


def write_md(path: Path, source: Path, body: str, today: str) -> None:
    fm = [
        "---",
        f"source: {source.name}",
        f"model: {MODEL_NAME}",
        f"transcribed: {today}",
        "---",
        "",
        f"# Transcript: {source.stem}",
        "",
        body if body else "(no speech detected)",
        "",
    ]
    path.write_text("\n".join(fm), encoding="utf-8")


def load_model(model_dir: str | None = None):
    """Load Handy's Parakeet model once. Shared by the CLI and the GUI app."""
    md = resolve_model_dir(model_dir)
    import onnx_asr  # imported lazily so --help stays instant

    return onnx_asr.load_model(
        MODEL_NAME,
        path=str(md),
        quantization="int8",
        providers=["CPUExecutionProvider"],
    )


def read_duration(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate())


def process_one(model, src: Path, out_dir: Path, today: str) -> dict:
    """Decode, transcribe, and write <name>.md + <name>.srt for one file.

    Returns metadata used by both the CLI and the GUI:
    {md_path, srt_path, full_text, duration_sec, cues}.
    """
    src = Path(src)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "audio.wav"
        decode_to_wav(src, wav)
        duration = read_duration(wav)
        full_text, cues = transcribe_file(model, wav)
    md_path = out_dir / (src.stem + ".md")
    srt_path = out_dir / (src.stem + ".srt")
    body = paragraphs_from_cues(cues) or full_text
    write_md(md_path, src, body, today)
    write_srt(srt_path, cues)
    return {
        "md_path": md_path,
        "srt_path": srt_path,
        "full_text": body or full_text,
        "duration_sec": duration,
        "cues": cues,
    }


def gather_inputs(paths) -> list[Path]:
    out: list[Path] = []
    seen = set()
    for p in paths:
        p = Path(p).expanduser()
        if p.is_dir():
            for f in sorted(p.iterdir()):
                if f.is_file() and f.suffix.lower() in AUDIO_EXTS and f not in seen:
                    seen.add(f)
                    out.append(f)
        elif p.is_file():
            if p.suffix.lower() in AUDIO_EXTS and p not in seen:
                seen.add(p)
                out.append(p)
            elif p not in seen:
                log(f"  skip (unsupported type): {p.name}")
        else:
            log(f"  skip (not found): {p}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Batch-transcribe audio/video using Handy's local Parakeet model."
    )
    ap.add_argument("paths", nargs="*", help="Files and/or folders. Default: ~/Transcribe-Inbox")
    ap.add_argument("--out", help="Write outputs to this directory instead of beside each source.")
    ap.add_argument("--force", action="store_true", help="Re-transcribe even if a .md already exists.")
    ap.add_argument("--model", help="Path to the Parakeet model directory (overrides auto-detect).")
    args = ap.parse_args()

    if not args.paths:
        DROP_DIR.mkdir(parents=True, exist_ok=True)
        log(f"No paths given; scanning drop folder {DROP_DIR}")
        args.paths = [str(DROP_DIR)]

    inputs = gather_inputs(args.paths)
    if not inputs:
        log("Nothing to transcribe.")
        return 0

    out_dir = Path(args.out).expanduser() if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    today = _dt.date.today().isoformat()

    # Determine which files actually need work before paying to load the model.
    todo = []
    skipped = 0
    for src in inputs:
        dest = out_dir if out_dir else src.parent
        md_path = dest / (src.stem + ".md")
        if md_path.exists() and not args.force:
            skipped += 1
            continue
        todo.append((src, dest))

    if not todo:
        log(f"All {len(inputs)} file(s) already transcribed (use --force to redo).")
        return 0

    model_dir = resolve_model_dir(args.model)
    log(f"Loading model {MODEL_NAME} from {model_dir} ...")
    model = load_model(str(model_dir))
    log("Model loaded.\n")

    done = failed = 0
    for src, dest in todo:
        log(f"-> {src.name}")
        try:
            res = process_one(model, src, dest, today)
            log(f"   wrote {res['md_path'].name} + {res['srt_path'].name} "
                f"({len(res['cues'])} cues)")
            done += 1
        except Exception as e:  # noqa: BLE001 - report and continue the batch
            log(f"   FAILED: {type(e).__name__}: {e}")
            failed += 1

    log(f"\nDone. {done} transcribed, {skipped} skipped, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
