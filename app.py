#!/usr/bin/env python3
"""Handy Transcribe — desktop app.

A pywebview window over the local Parakeet transcription engine (transcribe.py).
Drop audio/video files in, choose move/copy, transcribe, and keep a searchable
library. Audio auto-deletes after N days unless saved to the archive.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import webview

import transcribe as engine
from library import Library, load_settings, save_settings

WINDOW: webview.Window | None = None

AUDIO_EXTS = engine.AUDIO_EXTS


def unique_path(directory: Path, name: str) -> Path:
    """Return a non-colliding path inside directory for the given filename."""
    p = directory / name
    if not p.exists():
        return p
    stem, suffix = Path(name).stem, Path(name).suffix
    i = 2
    while True:
        cand = directory / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
        i += 1


def push(event: str, payload=None) -> None:
    """Send an event to the JS frontend."""
    if WINDOW is None:
        return
    data = json.dumps(payload if payload is not None else {})
    WINDOW.evaluate_js(f"window.pyEvent({json.dumps(event)}, {data})")


class Api:
    def __init__(self):
        self.settings = load_settings()
        self.lib = Library(self.settings["library_path"])
        self.model = None
        self.model_lock = threading.Lock()
        self.queue: list[dict] = []  # staged files awaiting transcription

    # ---- settings -----------------------------------------------------
    def get_settings(self):
        return self.settings

    def set_settings(self, new):
        self.settings = save_settings(new)
        # Re-point the library if the folder changed.
        if Path(self.settings["library_path"]).expanduser() != self.lib.root:
            self.lib = Library(self.settings["library_path"])
        return self.settings

    def choose_library_folder(self):
        res = WINDOW.create_file_dialog(webview.FileDialog.FOLDER)
        if res:
            return self.set_settings({"library_path": res[0]})
        return self.settings

    # ---- adding files -------------------------------------------------
    def pick_files(self):
        exts = " ".join(f"*{e}" for e in sorted(AUDIO_EXTS))
        res = WINDOW.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=True,
            file_types=(f"Audio/Video ({exts})", "All files (*.*)"),
        )
        return list(res) if res else []

    def add_paths(self, paths, disposition):
        """Stage dropped/picked files for transcription. Does not move yet."""
        added = []
        for p in paths or []:
            sp = Path(p)
            if sp.is_file() and sp.suffix.lower() in AUDIO_EXTS:
                item = {"src": str(sp), "name": sp.name, "disposition": disposition}
                self.queue.append(item)
                added.append({"name": sp.name})
        return {"queue": [q["name"] for q in self.queue], "added": len(added)}

    def clear_queue(self):
        self.queue = []
        return {"queue": []}

    # ---- transcription ------------------------------------------------
    def transcribe_queue(self):
        if not self.queue:
            return {"started": False}
        batch = self.queue
        self.queue = []
        threading.Thread(target=self._run_batch, args=(batch,), daemon=True).start()
        return {"started": True, "count": len(batch)}

    def _ensure_model(self):
        with self.model_lock:
            if self.model is None:
                push("status", {"msg": "Loading transcription model…"})
                self.model = engine.load_model()
                push("status", {"msg": ""})

    def _run_batch(self, batch):
        try:
            self._ensure_model()
        except Exception as e:  # noqa: BLE001
            push("error", {"msg": f"Could not load model: {e}"})
            return
        today = _dt.date.today().isoformat()
        days = int(self.settings.get("auto_delete_days", 7))
        results = []
        for i, item in enumerate(batch, 1):
            src = Path(item["src"])
            push("progress", {"i": i, "n": len(batch), "name": src.name})
            try:
                # Bring the audio into the library (pending), per disposition.
                media_dest = unique_path(self.lib.pending_dir, src.name)
                if item["disposition"] == "move" and src.exists():
                    shutil.move(str(src), str(media_dest))
                else:
                    shutil.copy2(str(src), str(media_dest))

                res = engine.process_one(
                    self.model, media_dest, self.lib.transcripts_dir, today
                )
                delete_after = (
                    _dt.date.today() + _dt.timedelta(days=days)
                ).isoformat()
                rid = self.lib.add_record(
                    original_name=src.name,
                    original_path=str(src),
                    media_path=str(media_dest),
                    md_path=str(res["md_path"]),
                    srt_path=str(res["srt_path"]),
                    disposition=item["disposition"],
                    retention="auto",
                    delete_after=delete_after,
                    duration_sec=res["duration_sec"],
                    full_text=res["full_text"],
                )
                results.append({
                    "id": rid,
                    "name": src.name,
                    "duration_sec": round(res["duration_sec"], 1),
                    "delete_after": delete_after,
                    "md_path": str(res["md_path"]),
                    "srt_path": str(res["srt_path"]),
                })
            except Exception as e:  # noqa: BLE001
                push("item_error", {"name": src.name, "msg": str(e)})
        push("batch_done", {"results": results, "auto_delete_days": days})

    # ---- retention ----------------------------------------------------
    def set_saved(self, ids, save):
        def mover(media_path):
            mp = Path(media_path)
            dest = unique_path(self.lib.archive_dir, mp.name)
            if mp.exists():
                shutil.move(str(mp), str(dest))
            return str(dest)

        self.lib.set_retention(ids, keep=bool(save), archive_mover=mover)
        return {"ok": True}

    def clean_expired(self):
        removed = self.lib.sweep_expired()
        return {"removed": removed, "count": len(removed)}

    # ---- library / search --------------------------------------------
    def search(self, query="", iso_year=None, iso_week=None, date_from=None, date_to=None):
        return self.lib.search(
            query=query or "",
            iso_year=iso_year, iso_week=iso_week,
            date_from=date_from or None, date_to=date_to or None,
        )

    def weeks(self):
        return self.lib.weeks_present()

    def get_record(self, rid):
        return self.lib.get(int(rid))

    def read_md(self, rid):
        rec = self.lib.get(int(rid))
        if rec and rec.get("md_path") and Path(rec["md_path"]).exists():
            return Path(rec["md_path"]).read_text(encoding="utf-8")
        return ""

    # ---- finder / open ------------------------------------------------
    def open_path(self, p):
        if p and Path(p).exists():
            subprocess.run(["open", p])
        return {"ok": True}

    def reveal(self, p):
        if p and Path(p).exists():
            subprocess.run(["open", "-R", p])
        return {"ok": True}


def _bind_drop(api: Api):
    from webview.dom import DOMEventHandler

    def on_drop(e):
        files = (e or {}).get("dataTransfer", {}).get("files", []) or []
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        paths = [p for p in paths if p]
        if paths:
            push("dropped_files", {"paths": paths})

    def on_loaded():
        doc = WINDOW.dom.document
        doc.events.dragenter += DOMEventHandler(lambda e: None, prevent_default=True)
        doc.events.dragover += DOMEventHandler(lambda e: None, prevent_default=True)
        doc.events.drop += DOMEventHandler(on_drop, prevent_default=True)

    WINDOW.events.loaded += on_loaded


def _selftest() -> int:
    """Headless check that the (possibly bundled) app can load the model and
    transcribe. Triggered by HANDY_SELFTEST=1; used to verify a built .app."""
    print("SELFTEST: loading model…", flush=True)
    model = engine.load_model()
    print("SELFTEST: model loaded OK", flush=True)
    f = os.environ.get("HANDY_SELFTEST_FILE")
    if f and Path(f).exists():
        res = engine.process_one(
            model, Path(f), Path(tempfile.mkdtemp()), _dt.date.today().isoformat()
        )
        print("SELFTEST: transcribed ->", (res["full_text"] or "")[:80], flush=True)
    print("SELFTEST: OK", flush=True)
    return 0


def main():
    global WINDOW
    if os.environ.get("HANDY_SELFTEST"):
        raise SystemExit(_selftest())
    api = Api()
    here = Path(__file__).resolve().parent
    WINDOW = webview.create_window(
        "Handy Transcribe",
        str(here / "web" / "index.html"),
        js_api=api,
        width=980,
        height=720,
        min_size=(760, 560),
    )
    _bind_drop(api)
    # Sweep expired audio at launch (before UI interaction).
    api.lib.sweep_expired()
    webview.start()


if __name__ == "__main__":
    main()
