"""Naming/review surface: rank people by how often they recur, render an HTML contact sheet.

The user opens the contact sheet, sees a representative face per person (most-recurring first
= the family), and names the handful that matter with the CLI. `render_face_contactsheet` is
pure; `person_rows` reads the store.
"""

from __future__ import annotations

from collections import defaultdict
from html import escape

from pydantic import BaseModel

from composerv.store.db import Store


class PersonRow(BaseModel):
    person_id: int
    name: str = ""
    sensitive: bool = False
    note: str = ""        # user-supplied note (role/relationship); empty = unnamed
    n_faces: int = 0
    n_clips: int = 0
    rep_crop: str = ""   # crop of this person's largest (most frontal-ish) face


def person_rows(store: Store, min_clips: int = 1) -> list[PersonRow]:
    """One row per person, ranked by recurrence (clips desc, faces desc). rep_crop is the
    crop of the person's largest face (by bbox height). Filtered to people in >= min_clips."""
    by_person = defaultdict(list)
    for f in store.all_faces():
        if f.person_id is not None:
            by_person[f.person_id].append(f)
    rows: list[PersonRow] = []
    for pid, faces in by_person.items():
        clips = {f.asset_path for f in faces}
        if len(clips) < min_clips:
            continue
        biggest = max(faces, key=lambda f: (f.bbox[3] - f.bbox[1]) if len(f.bbox) >= 4 else 0.0)
        p = store.get_person(pid)
        rows.append(PersonRow(
            person_id=pid, name=(p.name if p else ""), sensitive=(p.sensitive if p else False),
            note=(p.note if p else ""),
            n_faces=len(faces), n_clips=len(clips), rep_crop=biggest.crop_path,
        ))
    rows.sort(key=lambda r: (r.n_clips, r.n_faces), reverse=True)
    return rows


_STYLE = """<style>
 body{font-family:-apple-system,system-ui,sans-serif;margin:24px;background:#111;color:#eee}
 h1{font-size:20px} .grid{display:flex;flex-wrap:wrap;gap:12px}
 .p{width:150px;background:#1b1b1b;border-radius:8px;padding:8px;text-align:center}
 .p.named{outline:2px solid #4c8} .p img{width:130px;height:130px;object-fit:cover;border-radius:6px;background:#333}
 .nm{font-weight:600;margin-top:6px} .un{color:#c96;font-style:italic;margin-top:6px}
 .meta{font-size:12px;color:#aaa;margin-top:4px} .cmd{font-size:11px;color:#8ad;margin-top:4px}
 .sens{color:#e88}
</style>"""


def render_face_contactsheet(rows: list[PersonRow], title: str = "composerV people") -> str:
    parts = ["<!doctype html><html><head><meta charset='utf-8'>",
             f"<title>{escape(title)}</title>", _STYLE, "</head><body>",
             f"<h1>{escape(title)}</h1>",
             "<p class='meta'>most-recurring first. Name the family with: "
             "<code>composerv name &lt;id&gt; &lt;name&gt;</code></p>", "<div class='grid'>"]
    for r in rows:
        img = f'<img src="{escape(r.rep_crop)}">' if r.rep_crop else '<img>'
        if r.name:
            label = f"<div class='nm'>{escape(r.name)}{' 🔒' if r.sensitive else ''}</div>"
        else:
            label = "<div class='un'>unnamed</div>"
        cls = "p named" if r.name else "p"
        parts.append(
            f"<div class='{cls}'>{img}{label}"
            f"<div class='meta'>{r.n_clips} clips · {r.n_faces} faces</div>"
            f"<div class='cmd'>name {r.person_id} …</div></div>"
        )
    parts.append("</div></body></html>")
    return "\n".join(parts)
