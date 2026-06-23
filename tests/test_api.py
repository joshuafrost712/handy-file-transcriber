"""Backend / API tests — exercise the Api class directly, no GUI.

The Api methods are the contract the UI calls over the pywebview bridge, so
testing them headlessly covers the real logic (staging, move/copy, retention,
search) without driving native windows. The transcription engine is stubbed so
these stay fast and model-independent; real transcription is in
test_engine_e2e.py.
"""
import datetime as _dt
import wave
from pathlib import Path

import pytest

import app
import library
import transcribe as engine


def _wav(path: Path, seconds: float = 0.3) -> Path:
    n = int(seconds * engine.SAMPLE_RATE)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(engine.SAMPLE_RATE)
        wf.writeframes(b"\x00\x00" * n)
    return path


@pytest.fixture
def api(tmp_path, monkeypatch):
    """An Api wired to an isolated library + settings under tmp_path, with the
    transcription engine stubbed so no model is loaded."""
    cfg = tmp_path / "config"
    monkeypatch.setattr(library, "CONFIG_DIR", cfg)
    monkeypatch.setattr(library, "SETTINGS_PATH", cfg / "settings.json")
    lib_root = tmp_path / "lib"
    library.save_settings({"library_path": str(lib_root)})

    # Stub the engine: no model load, deterministic transcript output.
    monkeypatch.setattr(app.engine, "load_model", lambda *a, **k: object())

    def fake_process_one(model, src, out_dir, today):
        out_dir = Path(out_dir)
        md = out_dir / (Path(src).stem + ".md")
        srt = out_dir / (Path(src).stem + ".srt")
        md.write_text("transcript: hello world", encoding="utf-8")
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello world\n", encoding="utf-8")
        return {"md_path": md, "srt_path": srt, "full_text": "hello world",
                "duration_sec": 1.0, "cues": []}

    monkeypatch.setattr(app.engine, "process_one", fake_process_one)
    return app.Api()


def test_add_paths_filters_nonmedia(api, tmp_path):
    good = _wav(tmp_path / "rec.wav")
    bad = tmp_path / "notes.txt"; bad.write_text("nope")
    r = api.add_paths([str(good), str(bad)], "copy")
    assert r["added"] == 1
    assert r["queue"] == ["rec.wav"]


def test_clear_queue(api, tmp_path):
    api.add_paths([str(_wav(tmp_path / "a.wav"))], "copy")
    assert api.clear_queue() == {"queue": []}
    assert api.queue == []


def test_set_settings_repoints_library(api, tmp_path):
    new_root = tmp_path / "other-lib"
    api.set_settings({"library_path": str(new_root)})
    assert api.lib.root == new_root
    assert (new_root / "Transcripts").is_dir()


def test_run_batch_copy_keeps_original_and_indexes(api, tmp_path):
    src = _wav(tmp_path / "talk.wav")
    api._run_batch([{"src": str(src), "name": "talk.wav", "disposition": "copy"}])
    assert src.exists(), "copy disposition must leave the original in place"
    rows = api.lib.search()
    assert len(rows) == 1
    assert rows[0]["full_text"] == "hello world"
    assert rows[0]["original_name"] == "talk.wav"
    # Audio was brought into the pending area.
    assert Path(rows[0]["media_path"]).parent == api.lib.pending_dir


def test_run_batch_move_removes_original(api, tmp_path):
    src = _wav(tmp_path / "move-me.wav")
    api._run_batch([{"src": str(src), "name": "move-me.wav", "disposition": "move"}])
    assert not src.exists(), "move disposition must relocate the original"
    assert len(api.lib.search()) == 1


def test_set_saved_archives_audio(api, tmp_path):
    src = _wav(tmp_path / "keep.wav")
    api._run_batch([{"src": str(src), "name": "keep.wav", "disposition": "copy"}])
    rid = api.lib.search()[0]["id"]
    api.set_saved([rid], True)
    rec = api.lib.get(rid)
    assert rec["retention"] == "keep"
    assert Path(rec["media_path"]).parent == api.lib.archive_dir
    assert Path(rec["media_path"]).exists()


def test_clean_expired_deletes_due_media(api, tmp_path):
    media = _wav(api.lib.pending_dir / "old.wav")
    past = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    api.lib.add_record(
        original_name="old.wav", original_path=str(media), media_path=str(media),
        md_path="x.md", srt_path="x.srt", disposition="copy", retention="auto",
        delete_after=past, duration_sec=1.0, full_text="text",
    )
    r = api.clean_expired()
    assert r["count"] == 1
    assert not media.exists()


def test_search_by_text(api, tmp_path):
    for name, text in [("alpha.wav", "lions and tigers"), ("beta.wav", "bears only")]:
        api.lib.add_record(
            original_name=name, original_path=name, media_path=None,
            md_path="m", srt_path="s", disposition="copy", retention="auto",
            delete_after=None, duration_sec=1.0, full_text=text,
        )
    assert {r["original_name"] for r in api.search("tigers")} == {"alpha.wav"}
    assert len(api.search("")) == 2
