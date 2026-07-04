"""Adapters between the domain IntentionList and the preview engine's thin EDL format.

The preview EDL is deliberately decoupled from the domain models (the player is a thin
viewer): a dict {"fps": int, "clips": [ {kind: clip, file, in, out} | {kind: gap, duration} ]}.
STUB (TDD red).
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from composerv.models import IntentionList


def intention_to_edl(il: IntentionList, source_paths: Mapping[str, str], title: str = "") -> dict:
    clips: list[dict] = []
    for seg in il.segments:
        if not seg.enabled:
            continue
        if seg.kind == "gap":
            clips.append({"kind": "gap", "duration": seg.duration_s})
        else:
            clips.append(
                {
                    "kind": "clip",
                    "file": source_paths[seg.source_id],  # KeyError if we lack the source path
                    "in": seg.in_sec,
                    "out": seg.out_sec,
                }
            )
    edl: dict = {"fps": il.timeline_fps, "clips": clips}
    if title:
        edl["title"] = title  # the day/event label, burned onto the picture + shown in preview
    if il.music is not None:
        m = il.music
        edl["music"] = {
            "file": m.path,
            "gain_db": m.gain_db,
            "duck_db": m.duck_db,
            "fade_out_s": m.fade_out_s,
        }
        # Only add the highlight keys when there ARE highlights, so the no-highlight EDL
        # stays the original 4-key block (byte-for-byte back-compat).
        if m.highlights:
            edl["music"]["music_duck_db"] = m.music_duck_db
            edl["music"]["highlight_db"] = m.highlight_db
            edl["music"]["highlights"] = [
                {"start": h.start_s, "end": h.end_s, "ramp": h.ramp_s,
                 "music_duck_db": h.music_duck_db, "clip_db": h.clip_db, "label": h.label}
                for h in m.highlights
            ]
    return edl


def load_edl_file(path: str) -> tuple[list[dict], int, dict | None, str]:
    """Load an EDL JSON file -> (clips, fps, music, title). music is None when absent; title ''."""
    data = json.loads(open(path).read())
    return data.get("clips", []), int(data.get("fps", 30)), data.get("music"), data.get("title", "")
