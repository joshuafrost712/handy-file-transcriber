"""End-to-end transcription over real synthesized speech in several containers.

This is the "drop in any audio/video file and it works" guarantee: decode via
ffmpeg -> Parakeet -> .md + .srt, for each common format. Requires Handy's
Parakeet model; skips cleanly when it is absent (e.g. CI without Handy).
"""
import datetime as _dt
import os
from pathlib import Path

import pytest

import transcribe as engine
from conftest import model_available, transcode

pytestmark = pytest.mark.skipif(
    not model_available(), reason="Handy's Parakeet model is not installed"
)

TODAY = _dt.date.today().isoformat()


@pytest.fixture(scope="session")
def model():
    return engine.load_model()


@pytest.mark.parametrize("ext", [".wav", ".mp3", ".m4a", ".mp4", ".mov"])
def test_transcribes_each_format(model, speech_aiff, tmp_path, ext):
    src = transcode(speech_aiff, tmp_path / f"clip{ext}")
    res = engine.process_one(model, src, tmp_path / "out", TODAY)

    assert Path(res["md_path"]).exists()
    assert Path(res["srt_path"]).exists()
    assert res["duration_sec"] > 0
    text = (res["full_text"] or "").lower()
    assert text.strip(), f"no speech recognized from {ext}"
    # The synthesized phrase is "testing one two three four five"; require at
    # least one distinctive token rather than an exact match (ASR varies).
    assert any(w in text for w in ("test", "three", "four", "five")), text


@pytest.mark.skipif(
    not os.environ.get("HANDY_SLOW_TESTS"),
    reason="set HANDY_SLOW_TESTS=1 to run the >600s windowed-path test",
)
def test_long_file_windowed_path(model, speech_aiff, tmp_path):
    # Build a >MAX_WINDOW_SEC file with speech near the start AND past the
    # window boundary, so both windows must produce text.
    long_src = tmp_path / "long.wav"
    boundary = engine.MAX_WINDOW_SEC
    filt = (
        f"[0:a]adelay=0|0[a0];"
        f"[0:a]adelay={(boundary + 20) * 1000}|{(boundary + 20) * 1000}[a1];"
        f"[a0][a1]amix=inputs=2:duration=longest,apad=whole_dur={boundary + 60}"
    )
    import subprocess
    subprocess.run(
        [engine.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(speech_aiff), "-filter_complex", filt,
         "-ar", str(engine.SAMPLE_RATE), "-ac", "1", str(long_src)],
        check=True, capture_output=True,
    )
    res = engine.process_one(model, long_src, tmp_path / "out", TODAY)
    assert res["duration_sec"] > engine.MAX_WINDOW_SEC
    assert (res["full_text"] or "").strip()
    # Cues should span beyond the first window if the offset math is right.
    assert any(end > engine.MAX_WINDOW_SEC for _s, end, _t in res["cues"])
