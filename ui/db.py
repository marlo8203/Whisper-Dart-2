"""SQLite-backed persistence for transcripts and projects."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

from ui.constants import DB_FILE


class WhisperDB:
    """Thin sqlite3 wrapper for transcripts + projects."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS projects (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
        created_at  TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS transcripts (
        id                  TEXT PRIMARY KEY,
        timestamp           TEXT NOT NULL,
        title               TEXT DEFAULT '',
        audio_name          TEXT DEFAULT '',
        audio_path          TEXT DEFAULT '',
        model               TEXT,
        language            TEXT,
        task                TEXT,
        detected_language   TEXT,
        text                TEXT DEFAULT '',
        log                 TEXT DEFAULT '',
        project_id          TEXT,
        config_json         TEXT DEFAULT '{}',
        segments_json       TEXT DEFAULT '[]',
        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tx_project   ON transcripts(project_id);
    CREATE INDEX IF NOT EXISTS idx_tx_timestamp ON transcripts(timestamp DESC);
    CREATE TABLE IF NOT EXISTS app_settings (
        key    TEXT PRIMARY KEY,
        value  TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS tags (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
        color       TEXT NOT NULL DEFAULT '#5B4FE6',
        created_at  TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS transcript_tags (
        transcript_id  TEXT NOT NULL,
        tag_id         TEXT NOT NULL,
        PRIMARY KEY (transcript_id, tag_id),
        FOREIGN KEY (transcript_id) REFERENCES transcripts(id) ON DELETE CASCADE,
        FOREIGN KEY (tag_id)        REFERENCES tags(id)        ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_tt_transcript ON transcript_tags(transcript_id);
    """

    _UPDATABLE = {
        "title", "audio_name", "audio_path", "model", "language", "task",
        "detected_language", "text", "log", "project_id",
    }

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._init_schema()

    def _conn(self):
        c = sqlite3.connect(str(self.path), check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        return c

    def _init_schema(self):
        with self._lock, self._conn() as c:
            c.executescript(self.SCHEMA)
            # Idempotent migration for DBs that pre-date the audio_path column.
            try:
                c.execute("ALTER TABLE transcripts ADD COLUMN audio_path TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass

    @staticmethod
    def _row_to_entry(r) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        d["config"]   = json.loads(d.pop("config_json", "{}") or "{}")
        d["segments"] = json.loads(d.pop("segments_json", "[]") or "[]")
        return d

    # ── App settings / transcribe defaults ─────────────────────────────────
    def get_setting(self, key: str, default=None):
        with self._conn() as c:
            r = c.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        if not r:
            return default
        try:
            return json.loads(r["value"])
        except (ValueError, TypeError):
            return default

    def set_setting(self, key: str, value):
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    def get_defaults(self) -> dict:
        """Saved default transcribe settings for new transcripts."""
        return self.get_setting("transcribe_defaults", {}) or {}

    def set_default(self, key: str, value):
        """Update one default transcribe setting."""
        d = self.get_defaults()
        d[key] = value
        self.set_setting("transcribe_defaults", d)

    # ── Projects ──────────────────────────────────────────────────────────
    def all_projects(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in
                    c.execute("SELECT * FROM projects ORDER BY created_at")]

    def add_project(self, name: str) -> dict | None:
        name = (name or "").strip()
        if not name:
            return None
        pid = str(uuid.uuid4())
        ts  = datetime.now().isoformat()
        try:
            with self._lock, self._conn() as c:
                c.execute(
                    "INSERT INTO projects (id, name, created_at) VALUES (?, ?, ?)",
                    (pid, name, ts),
                )
        except sqlite3.IntegrityError:
            return None
        return {"id": pid, "name": name, "created_at": ts}

    # ── Tags ──────────────────────────────────────────────────────────────
    def all_tags(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in
                    c.execute("SELECT * FROM tags ORDER BY name COLLATE NOCASE")]

    def get_or_create_tag(self, name: str, color: str = "#5B4FE6") -> dict | None:
        """Return the tag with this name (case-insensitive), creating it if new."""
        name = (name or "").strip()
        if not name:
            return None
        with self._lock, self._conn() as c:
            r = c.execute("SELECT * FROM tags WHERE name=? COLLATE NOCASE", (name,)).fetchone()
            if r:
                return dict(r)
            tid = str(uuid.uuid4())
            ts = datetime.now().isoformat()
            c.execute(
                "INSERT INTO tags (id, name, color, created_at) VALUES (?, ?, ?, ?)",
                (tid, name, color, ts),
            )
            return {"id": tid, "name": name, "color": color, "created_at": ts}

    def delete_tag(self, tag_id: str):
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM tags WHERE id=?", (tag_id,))

    def toggle_transcript_tag(self, transcript_id: str, tag_id: str) -> bool:
        """Add the tag if absent, remove it if present. Returns True if now set."""
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM transcript_tags WHERE transcript_id=? AND tag_id=?",
                (transcript_id, tag_id),
            ).fetchone()
            if row:
                c.execute(
                    "DELETE FROM transcript_tags WHERE transcript_id=? AND tag_id=?",
                    (transcript_id, tag_id),
                )
                return False
            c.execute(
                "INSERT OR IGNORE INTO transcript_tags (transcript_id, tag_id) VALUES (?, ?)",
                (transcript_id, tag_id),
            )
            return True

    def _tags_for(self, c, transcript_id: str) -> list[dict]:
        return [dict(r) for r in c.execute(
            """SELECT t.* FROM tags t
               JOIN transcript_tags tt ON tt.tag_id = t.id
               WHERE tt.transcript_id = ?
               ORDER BY t.name COLLATE NOCASE""",
            (transcript_id,),
        )]

    # ── Transcripts ───────────────────────────────────────────────────────
    def all_transcripts(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM transcripts ORDER BY timestamp DESC").fetchall()
            # Batch-load all tag links in one pass to avoid N+1 queries.
            tag_map: dict[str, list[dict]] = {}
            for r in c.execute(
                """SELECT tt.transcript_id AS tid, t.id, t.name, t.color
                   FROM transcript_tags tt JOIN tags t ON t.id = tt.tag_id
                   ORDER BY t.name COLLATE NOCASE"""
            ):
                d = dict(r)
                tag_map.setdefault(d.pop("tid"), []).append(d)
            entries = []
            for r in rows:
                e = self._row_to_entry(r)
                e["tags"] = tag_map.get(e["id"], [])
                entries.append(e)
            return entries

    def get_transcript(self, eid: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM transcripts WHERE id=?", (eid,)).fetchone()
            e = self._row_to_entry(r)
            if e is not None:
                e["tags"] = self._tags_for(c, eid)
            return e

    def insert_transcript(self, entry: dict):
        with self._lock, self._conn() as c:
            c.execute("""
                INSERT INTO transcripts (
                    id, timestamp, title, audio_name, audio_path,
                    model, language, task,
                    detected_language, text, log, project_id,
                    config_json, segments_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry["id"], entry["timestamp"],
                entry.get("title", ""), entry.get("audio_name", ""),
                entry.get("audio_path", ""),
                entry.get("model"), entry.get("language"), entry.get("task"),
                entry.get("detected_language"), entry.get("text", ""),
                entry.get("log", ""), entry.get("project_id"),
                json.dumps(entry.get("config", {})),
                json.dumps(entry.get("segments", [])),
            ))

    def update_transcript(self, eid: str, **fields):
        if not fields:
            return
        cols, vals = [], []
        for k, v in fields.items():
            if k == "config":
                cols.append("config_json=?")
                vals.append(json.dumps(v))
            elif k == "segments":
                cols.append("segments_json=?")
                vals.append(json.dumps(v))
            elif k in self._UPDATABLE:
                cols.append(f"{k}=?")
                vals.append(v)
        if not cols:
            return
        vals.append(eid)
        with self._lock, self._conn() as c:
            c.execute(f"UPDATE transcripts SET {', '.join(cols)} WHERE id=?", vals)

    def delete_transcript(self, eid: str):
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM transcripts WHERE id=?", (eid,))


# Module-level singleton — initialized once at import time.
db = WhisperDB(DB_FILE)
