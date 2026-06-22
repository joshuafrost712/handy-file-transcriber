"""SQLite-backed library + paths for the Handy Transcribe app.

Stores one row per transcription, tracks audio retention (auto-delete vs keep),
and supports search by filename, by spoken content, and by date / ISO week.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path

APP_DIR_NAME = "HandyTranscribe"
DEFAULT_LIBRARY = Path.home() / "Transcribe Library"

# App config lives in Application Support so it survives moving the library.
CONFIG_DIR = Path.home() / "Library/Application Support" / APP_DIR_NAME
SETTINGS_PATH = CONFIG_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "library_path": str(DEFAULT_LIBRARY),
    "default_disposition": "ask",   # "move" | "copy" | "ask"
    "auto_delete_days": 7,
}


def load_settings() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            data.update(json.loads(SETTINGS_PATH.read_text()))
        except (ValueError, OSError):
            pass
    return data


def save_settings(settings: dict) -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    merged = load_settings()
    merged.update(settings or {})
    SETTINGS_PATH.write_text(json.dumps(merged, indent=2))
    return merged


class Library:
    """Wraps the library folder structure and its SQLite index."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser()
        self.transcripts_dir = self.root / "Transcripts"
        self.pending_dir = self.root / "Audio Pending"
        self.archive_dir = self.root / "Audio Archive"
        for d in (self.transcripts_dir, self.pending_dir, self.archive_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "library.db"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS transcriptions (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    original_name TEXT NOT NULL,
                    original_path TEXT,
                    media_path    TEXT,
                    md_path       TEXT,
                    srt_path      TEXT,
                    disposition   TEXT,
                    retention     TEXT DEFAULT 'auto',
                    delete_after  TEXT,
                    deleted       INTEGER DEFAULT 0,
                    duration_sec  REAL,
                    transcribed_at TEXT,
                    iso_year      INTEGER,
                    iso_week      INTEGER,
                    full_text     TEXT
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_week ON transcriptions(iso_year, iso_week)"
            )

    def add_record(
        self,
        *,
        original_name: str,
        original_path: str,
        media_path: str | None,
        md_path: str,
        srt_path: str,
        disposition: str,
        retention: str,
        delete_after: str | None,
        duration_sec: float,
        full_text: str,
        when: _dt.datetime | None = None,
    ) -> int:
        when = when or _dt.datetime.now()
        iso = when.isocalendar()
        with self._connect() as con:
            cur = con.execute(
                """
                INSERT INTO transcriptions (
                    original_name, original_path, media_path, md_path, srt_path,
                    disposition, retention, delete_after, deleted, duration_sec,
                    transcribed_at, iso_year, iso_week, full_text
                ) VALUES (?,?,?,?,?,?,?,?,0,?,?,?,?,?)
                """,
                (
                    original_name, original_path, media_path, md_path, srt_path,
                    disposition, retention, delete_after, duration_sec,
                    when.isoformat(timespec="seconds"), iso.year, iso.week, full_text,
                ),
            )
            return cur.lastrowid

    def set_retention(self, ids, keep: bool, archive_mover=None) -> None:
        """Mark rows keep/auto. When keeping, move pending audio to the archive
        via the provided archive_mover(src_path)->new_path callback."""
        with self._connect() as con:
            for rid in ids:
                row = con.execute(
                    "SELECT media_path, retention, deleted FROM transcriptions WHERE id=?",
                    (rid,),
                ).fetchone()
                if row is None:
                    continue
                media = row["media_path"]
                if keep:
                    new_media = media
                    if media and archive_mover:
                        new_media = archive_mover(media)
                    con.execute(
                        "UPDATE transcriptions SET retention='keep', delete_after=NULL,"
                        " media_path=? WHERE id=?",
                        (new_media, rid),
                    )
                else:
                    con.execute(
                        "UPDATE transcriptions SET retention='auto' WHERE id=?", (rid,)
                    )

    def sweep_expired(self, today: _dt.date | None = None) -> list[str]:
        """Delete media files past their auto-delete date. Returns deleted names."""
        today = today or _dt.date.today()
        removed = []
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, original_name, media_path FROM transcriptions"
                " WHERE retention='auto' AND deleted=0 AND delete_after IS NOT NULL"
                " AND delete_after <= ?",
                (today.isoformat(),),
            ).fetchall()
            for r in rows:
                mp = r["media_path"]
                if mp:
                    try:
                        Path(mp).unlink(missing_ok=True)
                    except OSError:
                        pass
                con.execute(
                    "UPDATE transcriptions SET deleted=1, media_path=NULL WHERE id=?",
                    (r["id"],),
                )
                removed.append(r["original_name"])
        return removed

    def search(self, query: str = "", iso_year=None, iso_week=None,
               date_from=None, date_to=None, limit: int = 500) -> list[dict]:
        sql = "SELECT * FROM transcriptions WHERE 1=1"
        params: list = []
        if query:
            sql += " AND (original_name LIKE ? OR full_text LIKE ?)"
            like = f"%{query}%"
            params += [like, like]
        if iso_year and iso_week:
            sql += " AND iso_year=? AND iso_week=?"
            params += [iso_year, iso_week]
        if date_from:
            sql += " AND transcribed_at >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND transcribed_at <= ?"
            params.append(date_to + "T23:59:59")
        sql += " ORDER BY transcribed_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as con:
            return [dict(r) for r in con.execute(sql, params).fetchall()]

    def weeks_present(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT iso_year, iso_week, COUNT(*) n FROM transcriptions"
                " GROUP BY iso_year, iso_week ORDER BY iso_year DESC, iso_week DESC"
            ).fetchall()
        out = []
        for r in rows:
            monday = _dt.date.fromisocalendar(r["iso_year"], r["iso_week"], 1)
            sunday = monday + _dt.timedelta(days=6)
            out.append({
                "iso_year": r["iso_year"],
                "iso_week": r["iso_week"],
                "count": r["n"],
                "label": f"{r['iso_year']}-W{r['iso_week']:02d} "
                         f"({monday.strftime('%b %-d')}–{sunday.strftime('%b %-d')})",
            })
        return out

    def get(self, rid: int) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM transcriptions WHERE id=?", (rid,)
            ).fetchone()
            return dict(row) if row else None
