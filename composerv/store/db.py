"""SQLite store for assets and captions.

Single-file, WAL-mode SQLite is the authoritative index (vectors via sqlite-vec added
later). Re-indexing replaces by source (delete-then-insert) so it is idempotent.
"""

from __future__ import annotations

import json
import os
import sqlite3

from pydantic import BaseModel

from composerv.analyze.base import CaptionResult
from composerv.index.frames import FrameRef
from composerv.index.probe import MediaInfo

# MediaInfo columns persisted in `assets` (path is the primary key).
_ASSET_COLS = [
    "kind", "codec", "width", "height", "fps_num", "fps_den", "is_vfr", "pix_fmt",
    "bit_depth", "is_hdr", "has_audio", "audio_sample_rate", "audio_channels",
    "duration_s", "capture_time", "camera_proxy", "proxy_path",
]
_BOOL_COLS = {"is_vfr", "is_hdr", "has_audio"}


class StoredCaption(BaseModel):
    frame_index: int
    src_pts_s: float
    backend: str
    caption: str
    shot_type: str
    objects: list[str]
    ocr_text: str
    salience: float


class ClarityRecord(BaseModel):
    """Per-clip state for the clarity + selection layer."""
    summary: str = ""          # the "what is this" description shown to the user
    source: str = ""           # "" (none yet) | "local" | "claude" (refined)
    selected: bool = False     # in the user's working set


class ClipAesthetics(BaseModel):
    """Per-clip aesthetics: the best instant + the full score curve (raw, for the executor)."""
    best_t: float | None = None
    curve: list[tuple[float, float, bool]] = []   # (t_seconds, overall_score, is_utility)


class StoredFace(BaseModel):
    face_id: int
    asset_path: str
    t: float
    bbox: list[float]
    embedding: list[float]
    person_id: int | None = None
    crop_path: str = ""   # a saved thumbnail of just this face, for the naming contact sheet


class Person(BaseModel):
    person_id: int
    name: str = ""
    sensitive: bool = False
    centroid: list[float] = []   # the person's running-mean face embedding (the gallery)
    n_faces: int = 0             # how many faces back this centroid
    note: str = ""               # a one-line human note (role/relationship), shown to the director


class Brief(BaseModel):
    """Per-scope user input: freeform context + style, injected into the director as high priority."""
    scope: str
    context: str = ""
    style: str = ""
    updated_at: str = ""


def _dump_objects(objects) -> str:
    """Serialize grounded objects (GroundedObject models or {label, box} dicts) to JSON for storage."""
    out = []
    for o in objects or []:
        d = o.model_dump() if hasattr(o, "model_dump") else o if isinstance(o, dict) else None
        if d is None:
            continue
        out.append({"label": str(d.get("label", "")), "box": [float(x) for x in (d.get("box") or [])]})
    return json.dumps(out)


class Store:
    def __init__(self, path: str):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)   # so a default under ~/Movies/composerV just works
        self.path = path                          # kept so threaded callers can reopen (sqlite
        self.conn = sqlite3.connect(path)         # connections are single-thread)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        cols = ",\n".join(f"{c} {'INTEGER' if c in _BOOL_COLS else ''}".strip() for c in _ASSET_COLS)
        self.conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS assets (
                path TEXT PRIMARY KEY,
                {cols}
            );
            CREATE TABLE IF NOT EXISTS captions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_path TEXT NOT NULL,
                backend TEXT NOT NULL,
                frame_index INTEGER NOT NULL,
                src_pts_s REAL NOT NULL,
                caption TEXT, shot_type TEXT, objects TEXT, ocr_text TEXT, salience REAL,
                FOREIGN KEY (asset_path) REFERENCES assets(path)
            );
            CREATE INDEX IF NOT EXISTS captions_by_asset ON captions(asset_path, backend);
            CREATE TABLE IF NOT EXISTS clip_summaries (
                asset_path TEXT PRIMARY KEY,
                summary TEXT
            );
            CREATE TABLE IF NOT EXISTS clarity (
                asset_path TEXT PRIMARY KEY,
                summary TEXT DEFAULT '',
                source TEXT DEFAULT '',
                selected INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS keyframes (
                asset_path TEXT NOT NULL,
                t REAL NOT NULL,
                thumb_path TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS keyframes_by_asset ON keyframes(asset_path);
            CREATE TABLE IF NOT EXISTS clip_moments (
                asset_path TEXT NOT NULL,
                t REAL NOT NULL,
                text TEXT NOT NULL,
                ocr TEXT DEFAULT '',
                objects TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS clip_moments_by_asset ON clip_moments(asset_path);
            CREATE TABLE IF NOT EXISTS clip_aesthetics (
                asset_path TEXT PRIMARY KEY,
                best_t REAL,
                curve TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS reframe_tracks (
                asset_path TEXT PRIMARY KEY,
                track TEXT
            );
            CREATE TABLE IF NOT EXISTS transcript (
                asset_path TEXT NOT NULL,
                start REAL NOT NULL,
                end REAL NOT NULL,
                text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS transcript_by_asset ON transcript(asset_path);
            CREATE TABLE IF NOT EXISTS persons (
                person_id INTEGER PRIMARY KEY,
                name TEXT DEFAULT '',
                sensitive INTEGER DEFAULT 0,
                centroid TEXT DEFAULT '',
                n_faces INTEGER DEFAULT 0,
                note TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS faces (
                face_id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_path TEXT NOT NULL,
                t REAL NOT NULL,
                bbox TEXT,
                embedding TEXT,
                person_id INTEGER,
                crop_path TEXT DEFAULT '',
                FOREIGN KEY (asset_path) REFERENCES assets(path)
            );
            CREATE INDEX IF NOT EXISTS faces_by_asset ON faces(asset_path);
            CREATE TABLE IF NOT EXISTS briefs (
                scope TEXT PRIMARY KEY,
                context TEXT DEFAULT '',
                style TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            );
            """
        )
        self.conn.commit()
        # migrate DBs created before these columns existed (CREATE IF NOT EXISTS won't add them)
        self._ensure_columns("persons", {"centroid": "TEXT DEFAULT ''", "n_faces": "INTEGER DEFAULT 0",
                                          "note": "TEXT DEFAULT ''"})
        self._ensure_columns("faces", {"crop_path": "TEXT DEFAULT ''"})
        self._ensure_columns("clip_moments", {"ocr": "TEXT DEFAULT ''", "objects": "TEXT DEFAULT ''"})

    def _ensure_columns(self, table: str, cols: dict[str, str]) -> None:
        existing = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in existing:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        self.conn.commit()

    def set_clip_summary(self, asset_path: str, summary: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO clip_summaries (asset_path, summary) VALUES (?, ?)",
            (asset_path, summary),
        )
        self.conn.commit()

    def get_clip_summary(self, asset_path: str) -> str:
        row = self.conn.execute(
            "SELECT summary FROM clip_summaries WHERE asset_path = ?", (asset_path,)
        ).fetchone()
        return (row["summary"] if row else "") or ""

    # --- clarity + selection layer ---

    def set_clarity_summary(self, asset_path: str, summary: str, source: str = "local") -> None:
        """Set the 'what is this' description + its source; preserve the selection flag."""
        self.conn.execute(
            """INSERT INTO clarity (asset_path, summary, source) VALUES (?, ?, ?)
               ON CONFLICT(asset_path) DO UPDATE SET summary=excluded.summary, source=excluded.source""",
            (asset_path, summary, source),
        )
        self.conn.commit()

    def set_selected(self, asset_path: str, selected: bool) -> None:
        """Add/remove a clip from the working set; preserve any existing summary/source."""
        self.conn.execute(
            """INSERT INTO clarity (asset_path, selected) VALUES (?, ?)
               ON CONFLICT(asset_path) DO UPDATE SET selected=excluded.selected""",
            (asset_path, 1 if selected else 0),
        )
        self.conn.commit()

    def get_clarity(self, asset_path: str) -> ClarityRecord:
        row = self.conn.execute(
            "SELECT summary, source, selected FROM clarity WHERE asset_path = ?", (asset_path,)
        ).fetchone()
        if row is None:
            return ClarityRecord()
        return ClarityRecord(
            summary=row["summary"] or "", source=row["source"] or "", selected=bool(row["selected"])
        )

    def list_selected(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT asset_path FROM clarity WHERE selected = 1 ORDER BY asset_path"
        ).fetchall()
        return [r["asset_path"] for r in rows]

    def set_keyframes(self, asset_path: str, items: list[tuple[float, str]]) -> None:
        """Replace the display keyframes (t, thumb_path) for a clip."""
        self.conn.execute("DELETE FROM keyframes WHERE asset_path = ?", (asset_path,))
        self.conn.executemany(
            "INSERT INTO keyframes (asset_path, t, thumb_path) VALUES (?, ?, ?)",
            [(asset_path, float(t), p) for t, p in items],
        )
        self.conn.commit()

    def get_keyframes(self, asset_path: str) -> list[tuple[float, str]]:
        rows = self.conn.execute(
            "SELECT t, thumb_path FROM keyframes WHERE asset_path = ? ORDER BY t, rowid",
            (asset_path,),
        ).fetchall()
        return [(r["t"], r["thumb_path"]) for r in rows]

    # --- perception index: the cached footage table (so the director needn't re-run models) ---

    def set_clip_moments(self, asset_path: str, items: list[tuple]) -> None:
        """Replace the per-moment visual index for a clip. Each item is (t, text) or
        (t, text, ocr) or (t, text, ocr, objects), where objects is a list of GroundedObject
        (or {label, box} dicts). The on-screen text and grounded boxes default to empty."""
        # build (and validate) every row BEFORE the DELETE, so a malformed item can never leave
        # the table emptied (it would otherwise raise after DELETE, losing the cached moments)
        rows = []
        for it in items:
            if len(it) < 2:
                raise ValueError(f"clip moment needs at least (t, text); got {it!r}")
            t, txt = it[0], it[1]
            ocr = str(it[2]) if len(it) > 2 else ""
            objects = _dump_objects(it[3]) if len(it) > 3 else ""
            rows.append((asset_path, float(t), str(txt), ocr, objects))
        self.conn.execute("DELETE FROM clip_moments WHERE asset_path = ?", (asset_path,))
        self.conn.executemany(
            "INSERT INTO clip_moments (asset_path, t, text, ocr, objects) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def get_clip_moments(self, asset_path: str) -> list[tuple[float, str]]:
        rows = self.conn.execute(
            "SELECT t, text FROM clip_moments WHERE asset_path = ? ORDER BY t, rowid",
            (asset_path,),
        ).fetchall()
        return [(r["t"], r["text"]) for r in rows]

    def get_clip_moments_rich(self, asset_path: str):
        """Per-moment visual index WITH on-screen text + grounded boxes -> list[ClipMoment].
        Used by the director table (OCR) and reframe (boxes); get_clip_moments stays (t, text)."""
        from composerv.analyze.clip_video import ClipMoment, GroundedObject

        rows = self.conn.execute(
            "SELECT t, text, ocr, objects FROM clip_moments WHERE asset_path = ? ORDER BY t, rowid",
            (asset_path,),
        ).fetchall()
        out = []
        for r in rows:
            # tolerate out-of-band / legacy 'objects' values: the director reads this getter and a
            # single bad row must not abort the edit. Drop unparseable boxes, keep t/text/ocr.
            try:
                raw = json.loads(r["objects"] or "[]")
            except (json.JSONDecodeError, TypeError):
                raw = []
            objs = []
            for o in raw if isinstance(raw, list) else []:
                if not isinstance(o, dict):
                    continue
                try:
                    objs.append(GroundedObject(**o))
                except Exception:
                    continue
            out.append(ClipMoment(t=r["t"], text=r["text"], ocr=r["ocr"] or "", objects=objs))
        return out

    def set_clip_aesthetics(self, asset_path: str, best_t: float | None, curve) -> None:
        """Replace a clip's aesthetics (best instant + raw score curve)."""
        payload = json.dumps([[float(t), float(s), bool(u)] for t, s, u in curve])
        self.conn.execute(
            """INSERT INTO clip_aesthetics (asset_path, best_t, curve) VALUES (?,?,?)
               ON CONFLICT(asset_path) DO UPDATE SET best_t=excluded.best_t, curve=excluded.curve""",
            (asset_path, best_t, payload),
        )
        self.conn.commit()

    def get_clip_aesthetics(self, asset_path: str):
        """-> ClipAesthetics, or None if this clip has no aesthetics cached yet."""
        row = self.conn.execute(
            "SELECT best_t, curve FROM clip_aesthetics WHERE asset_path = ?", (asset_path,)
        ).fetchone()
        if row is None:
            return None
        try:
            raw = json.loads(row["curve"] or "[]")
        except (json.JSONDecodeError, TypeError):
            raw = []
        curve = [(float(c[0]), float(c[1]), bool(c[2]))
                 for c in raw if isinstance(c, (list, tuple)) and len(c) == 3]
        return ClipAesthetics(best_t=row["best_t"], curve=curve)

    def set_reframe_track(self, path, track):
        self.conn.execute("INSERT OR REPLACE INTO reframe_tracks (asset_path, track) VALUES (?, ?)",
                          (path, json.dumps([list(s) for s in track]))); self.conn.commit()

    def get_reframe_track(self, path):
        r = self.conn.execute("SELECT track FROM reframe_tracks WHERE asset_path=?", (path,)).fetchone()
        return [tuple(s) for s in json.loads(r["track"])] if r else []

    def set_transcript(self, asset_path: str, items: list[tuple[float, float, str]]) -> None:
        """Replace the per-sentence transcript (start, end, text) for a clip (the speech half)."""
        self.conn.execute("DELETE FROM transcript WHERE asset_path = ?", (asset_path,))
        self.conn.executemany(
            "INSERT INTO transcript (asset_path, start, end, text) VALUES (?, ?, ?, ?)",
            [(asset_path, float(s), float(e), str(txt)) for s, e, txt in items],
        )
        self.conn.commit()

    def get_transcript(self, asset_path: str) -> list[tuple[float, float, str]]:
        rows = self.conn.execute(
            "SELECT start, end, text FROM transcript WHERE asset_path = ? ORDER BY start, rowid",
            (asset_path,),
        ).fetchall()
        return [(r["start"], r["end"], r["text"]) for r in rows]

    # --- faces + persons ---

    @staticmethod
    def _row_to_face(r) -> StoredFace:
        return StoredFace(
            face_id=r["face_id"], asset_path=r["asset_path"], t=r["t"],
            bbox=json.loads(r["bbox"] or "[]"), embedding=json.loads(r["embedding"] or "[]"),
            person_id=r["person_id"], crop_path=r["crop_path"] or "",
        )

    def replace_faces(self, asset_path: str, items: list[tuple]) -> None:
        """Replace the detected faces for a clip. items = [(t, bbox, embedding[, crop_path])]."""
        self.conn.execute("DELETE FROM faces WHERE asset_path = ?", (asset_path,))
        self.conn.executemany(
            "INSERT INTO faces (asset_path, t, bbox, embedding, crop_path) VALUES (?,?,?,?,?)",
            [(asset_path, float(it[0]), json.dumps([float(x) for x in it[1]]),
              json.dumps([float(x) for x in it[2]]), it[3] if len(it) > 3 else "")
             for it in items],
        )
        self.conn.commit()

    def reassign_faces(self, from_person: int, to_person: int) -> None:
        self.conn.execute("UPDATE faces SET person_id = ? WHERE person_id = ?",
                          (to_person, from_person))
        self.conn.commit()

    def delete_person(self, person_id: int) -> None:
        self.conn.execute("DELETE FROM persons WHERE person_id = ?", (person_id,))
        self.conn.commit()

    def get_faces(self, asset_path: str) -> list[StoredFace]:
        rows = self.conn.execute(
            "SELECT * FROM faces WHERE asset_path = ? ORDER BY t, face_id", (asset_path,)
        ).fetchall()
        return [self._row_to_face(r) for r in rows]

    def all_faces(self) -> list[StoredFace]:
        rows = self.conn.execute(
            "SELECT * FROM faces ORDER BY asset_path, t, face_id"
        ).fetchall()
        return [self._row_to_face(r) for r in rows]

    def set_face_person(self, face_id: int, person_id: int | None) -> None:
        self.conn.execute("UPDATE faces SET person_id = ? WHERE face_id = ?", (person_id, face_id))
        self.conn.commit()

    def upsert_person(self, person_id: int, name: str = "", sensitive: bool = False) -> None:
        """Create a person row if absent; never clobber an existing name/flag."""
        self.conn.execute(
            "INSERT OR IGNORE INTO persons (person_id, name, sensitive) VALUES (?,?,?)",
            (person_id, name, 1 if sensitive else 0),
        )
        self.conn.commit()

    def set_person_name(self, person_id: int, name: str, sensitive: bool = False) -> None:
        self.conn.execute(
            """INSERT INTO persons (person_id, name, sensitive) VALUES (?,?,?)
               ON CONFLICT(person_id) DO UPDATE SET name=excluded.name, sensitive=excluded.sensitive""",
            (person_id, name, 1 if sensitive else 0),
        )
        self.conn.commit()

    def set_person_note(self, person_id: int, note: str) -> None:
        """Set a person's one-line note, preserving name/sensitive/centroid."""
        self.conn.execute(
            """INSERT INTO persons (person_id, note) VALUES (?,?)
               ON CONFLICT(person_id) DO UPDATE SET note=excluded.note""",
            (person_id, note),
        )
        self.conn.commit()

    def clip_person_labels(self, asset_path: str, include_sensitive: bool = True) -> list[str]:
        """Named people in a clip as director labels: '名字（备注）' when a note exists, else '名字'."""
        labels = []
        for pid in self.clip_person_ids(asset_path):
            p = self.get_person(pid)
            if p and p.name and (include_sensitive or not p.sensitive):
                labels.append(f"{p.name}（{p.note}）" if p.note else p.name)
        return labels

    @staticmethod
    def _row_to_person(r) -> Person:
        return Person(
            person_id=r["person_id"], name=r["name"] or "", sensitive=bool(r["sensitive"]),
            centroid=json.loads(r["centroid"] or "[]"), n_faces=r["n_faces"] or 0,
            note=(r["note"] if "note" in r.keys() else "") or "",
        )

    def set_person_centroid(self, person_id: int, centroid: list[float], n_faces: int) -> None:
        """Persist a person's running-mean embedding (the family gallery), preserving name/flag."""
        self.conn.execute(
            """INSERT INTO persons (person_id, centroid, n_faces) VALUES (?,?,?)
               ON CONFLICT(person_id) DO UPDATE SET centroid=excluded.centroid, n_faces=excluded.n_faces""",
            (person_id, json.dumps([float(x) for x in centroid]), int(n_faces)),
        )
        self.conn.commit()

    def get_person(self, person_id: int) -> Person | None:
        r = self.conn.execute(
            "SELECT * FROM persons WHERE person_id = ?", (person_id,)
        ).fetchone()
        return self._row_to_person(r) if r is not None else None

    def list_persons(self) -> list[Person]:
        rows = self.conn.execute("SELECT * FROM persons ORDER BY person_id").fetchall()
        return [self._row_to_person(r) for r in rows]

    def named_gallery(self) -> list[tuple[int, list[float]]]:
        """Known people: (person_id, centroid) for those that are named AND have a centroid.
        This is what seeds recognition of new faces against the growing family database."""
        out = []
        for p in self.list_persons():
            if p.name and p.centroid:
                out.append((p.person_id, p.centroid))
        return out

    def clip_person_ids(self, asset_path: str) -> list[int]:
        rows = self.conn.execute(
            "SELECT DISTINCT person_id FROM faces WHERE asset_path = ? AND person_id IS NOT NULL "
            "ORDER BY person_id", (asset_path,)
        ).fetchall()
        return [r["person_id"] for r in rows]

    def clip_person_names(self, asset_path: str, include_sensitive: bool = True) -> list[str]:
        """Named people present in a clip (skips unnamed clusters; optionally gates sensitive)."""
        names = []
        for pid in self.clip_person_ids(asset_path):
            p = self.get_person(pid)
            if p and p.name and (include_sensitive or not p.sensitive):
                names.append(p.name)
        return names

    # --- briefs ---

    def set_brief(self, scope: str, context: str, style: str, updated_at: str = "") -> None:
        self.conn.execute(
            """INSERT INTO briefs (scope, context, style, updated_at) VALUES (?,?,?,?)
               ON CONFLICT(scope) DO UPDATE SET context=excluded.context, style=excluded.style,
               updated_at=excluded.updated_at""",
            (scope, context, style, updated_at),
        )
        self.conn.commit()

    def get_brief(self, scope: str) -> Brief | None:
        r = self.conn.execute("SELECT * FROM briefs WHERE scope = ?", (scope,)).fetchone()
        if r is None:
            return None
        return Brief(scope=r["scope"], context=r["context"] or "", style=r["style"] or "",
                     updated_at=r["updated_at"] or "")

    def upsert_asset(self, media: MediaInfo, proxy_path: str | None = None) -> None:
        data = media.model_dump()
        if proxy_path is not None:
            data["proxy_path"] = proxy_path
        values = [data["path"]] + [
            int(data[c]) if c in _BOOL_COLS else data[c] for c in _ASSET_COLS
        ]
        placeholders = ",".join("?" * (len(_ASSET_COLS) + 1))
        self.conn.execute(
            f"INSERT OR REPLACE INTO assets (path, {','.join(_ASSET_COLS)}) VALUES ({placeholders})",
            values,
        )
        self.conn.commit()

    def get_asset(self, path: str) -> MediaInfo | None:
        row = self.conn.execute("SELECT * FROM assets WHERE path = ?", (path,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        for c in _BOOL_COLS:
            d[c] = bool(d[c])
        return MediaInfo(**d)

    def list_assets(self) -> list[MediaInfo]:
        rows = self.conn.execute("SELECT path FROM assets ORDER BY path").fetchall()
        return [self.get_asset(r["path"]) for r in rows]

    def replace_captions(
        self, asset_path: str, backend: str, items: list[tuple[FrameRef, CaptionResult]]
    ) -> None:
        self.conn.execute(
            "DELETE FROM captions WHERE asset_path = ? AND backend = ?", (asset_path, backend)
        )
        self.conn.executemany(
            """INSERT INTO captions
               (asset_path, backend, frame_index, src_pts_s, caption, shot_type, objects, ocr_text, salience)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [
                (asset_path, backend, fr.index, fr.src_pts_s, cr.caption, cr.shot_type,
                 json.dumps(cr.objects), cr.ocr_text, cr.salience)
                for fr, cr in items
            ],
        )
        self.conn.commit()

    def get_captions(self, asset_path: str) -> list[StoredCaption]:
        rows = self.conn.execute(
            "SELECT * FROM captions WHERE asset_path = ? ORDER BY frame_index, id", (asset_path,)
        ).fetchall()
        return [
            StoredCaption(
                frame_index=r["frame_index"], src_pts_s=r["src_pts_s"], backend=r["backend"],
                caption=r["caption"] or "", shot_type=r["shot_type"] or "unknown",
                objects=json.loads(r["objects"] or "[]"), ocr_text=r["ocr_text"] or "",
                salience=r["salience"] or 0.0,
            )
            for r in rows
        ]
