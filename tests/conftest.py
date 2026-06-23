"""Shared test fixtures for Handy Transcribe.

Puts the repo root on sys.path (so `import app/transcribe/library` works) and
provides small synthesized-media fixtures built with macOS `say` + ffmpeg.
"""
from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import transcribe as engine  # noqa: E402


def _write_silent_wav(path: Path, seconds: float = 0.5) -> Path:
    """A valid 16 kHz mono PCM16 WAV of silence (no external tools needed)."""
    n = int(seconds * engine.SAMPLE_RATE)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(engine.SAMPLE_RATE)
        wf.writeframes(b"\x00\x00" * n)
    return path


@pytest.fixture
def silent_wav(tmp_path) -> Path:
    """A tiny valid audio file for non-transcription tests (no model needed)."""
    return _write_silent_wav(tmp_path / "silent.wav")


@pytest.fixture(scope="session")
def speech_aiff(tmp_path_factory) -> Path:
    """Real synthesized speech via macOS `say`, for end-to-end transcription."""
    if not Path("/usr/bin/say").exists():
        pytest.skip("macOS `say` not available")
    out = tmp_path_factory.mktemp("speech") / "sample.aiff"
    subprocess.run(
        ["say", "-o", str(out), "testing one two three four five"],
        check=True, capture_output=True,
    )
    return out


def transcode(src: Path, dst: Path) -> Path:
    """Transcode src into dst's container/codec via ffmpeg (audio only)."""
    subprocess.run(
        [engine.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), str(dst)],
        check=True, capture_output=True,
    )
    return dst


def model_available() -> bool:
    return engine.DEFAULT_MODEL_DIR.is_dir() or any(
        engine.DEFAULT_MODEL_DIR.parent.glob("parakeet-tdt-0.6b-v3*")
    ) if engine.DEFAULT_MODEL_DIR.parent.is_dir() else False
