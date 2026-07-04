"""Edit-decision storyboard: a visual of which clips and which time ranges the cut uses.

Companion to the FCPXML handoff. For each shot: the source clip's keyframe as background and
a timeline bar of the FULL clip with the used [in, out] range highlighted green (so you see
where in the clip it came from and how much of it). `render_edit_storyboard` is pure.
"""

from __future__ import annotations

import os
from html import escape

from pydantic import BaseModel

from composerv.index.when import capture_label
from composerv.models import IntentionList
from composerv.store.db import Store


class EditShot(BaseModel):
    order: int
    clip_id: str = ""
    keyframe: str = ""
    clip_duration: float = 0.0   # full source duration (the bar length)
    in_sec: float = 0.0
    out_sec: float = 0.0
    label: str = ""
    when: str = ""               # capture label for review, e.g. "2026年1月1日 下午"
    is_gap: bool = False


def build_edit_shots(il: IntentionList, store: Store) -> list[EditShot]:
    """Turn an IntentionList into shots: pull each clip's full duration + a keyframe near its
    in-point from the store (so the storyboard shows the used slice against the whole clip)."""
    shots: list[EditShot] = []
    for i, seg in enumerate(il.segments):
        if seg.kind == "gap" or not seg.source_id:
            shots.append(EditShot(order=i, is_gap=True, in_sec=0.0, out_sec=seg.duration_s,
                                  label=seg.label or "gap"))
            continue
        asset = store.get_asset(seg.source_id)
        dur = (asset.duration_s if asset and asset.duration_s else 0.0) or seg.out_sec
        kfs = store.get_keyframes(seg.source_id)
        kf = min(kfs, key=lambda tp: abs(tp[0] - seg.in_sec))[1] if kfs else ""
        shots.append(EditShot(
            order=i, clip_id=os.path.basename(seg.source_id), keyframe=kf, clip_duration=dur,
            in_sec=seg.in_sec, out_sec=seg.out_sec, label=seg.label or seg.note or "",
            when=capture_label(asset.capture_time if asset else None),
        ))
    return shots


_STYLE = """<style>
 body{font-family:-apple-system,system-ui,sans-serif;margin:24px;background:#111;color:#eee}
 h1{font-size:20px} .sub{color:#aaa;font-size:13px;margin-bottom:14px}
 .shot{display:flex;gap:12px;align-items:center;padding:8px;margin:6px 0;background:#1b1b1b;border-radius:8px}
 .shot.gap{opacity:.7;font-style:italic}
 .thumb{width:120px;height:68px;border-radius:6px;background:#333 center/cover no-repeat;flex:none}
 .body{flex:1} .lbl{font-size:14px} .lbl b{color:#fff}
 .bar{position:relative;height:14px;background:#333;border-radius:7px;margin:8px 0;overflow:hidden}
 .used{position:absolute;top:0;bottom:0;background:#3c6;border-radius:7px}
 .meta{font-size:12px;color:#aaa}
 .event{color:#e9c46a;font-size:15px;margin:-6px 0 14px}
 .when{color:#7fd1c0;font-size:12px;font-weight:600}
 .day{margin:18px 0 6px;padding-bottom:4px;border-bottom:1px solid #333;color:#e9c46a;font-size:14px}
</style>"""


def _day_of(when: str) -> str:
    return when.split(" ")[0] if when else ""


def render_edit_storyboard(shots: list[EditShot], title: str = "composerV cut",
                           event: str = "") -> str:
    total = sum(max(0.0, s.out_sec - s.in_sec) for s in shots)
    parts = ["<!doctype html><html><head><meta charset='utf-8'>", f"<title>{escape(title)}</title>",
             _STYLE, "</head><body>", f"<h1>{escape(title)}</h1>"]
    if event:
        parts.append(f"<div class='event'>{escape(event)}</div>")
    parts.append(f"<div class='sub'>{len(shots)} shots · {total:.0f}s total</div>")
    cur_day = None
    for s in shots:
        day = _day_of(s.when)
        if day and day != cur_day:           # a new day -> divider so review groups by date
            parts.append(f"<div class='day'>{escape(day)}</div>")
            cur_day = day
        if s.is_gap:
            parts.append(
                f"<div class='shot gap'><div class='thumb'></div><div class='body'>"
                f"<div class='lbl'>#{s.order} {escape(s.label or 'gap')}</div>"
                f"<div class='meta'>gap · {s.out_sec - s.in_sec:.1f}s</div></div></div>")
            continue
        dur = s.clip_duration or max(s.out_sec, 1.0)
        left = s.in_sec / dur * 100 if dur else 0.0
        width = (s.out_sec - s.in_sec) / dur * 100 if dur else 100.0
        bg = f"background-image:url('{escape(s.keyframe)}')" if s.keyframe else ""
        parts.append(
            f"<div class='shot'>"
            f"<div class='thumb' style=\"{bg}\"></div>"
            f"<div class='body'>"
            f"<div class='lbl'>#{s.order} <b>{escape(s.clip_id)}</b>"
            f"{(' <span class=when>' + escape(s.when) + '</span>') if s.when else ''}"
            f"{(' · ' + escape(s.label)) if s.label else ''}</div>"
            f"<div class='bar'><div class='used' style='left:{left:.1f}%;width:{width:.1f}%'></div></div>"
            f"<div class='meta'>{s.in_sec:.1f}–{s.out_sec:.1f}s of {dur:.0f}s "
            f"({width:.0f}% of clip)</div>"
            f"</div></div>")
    parts.append("</body></html>")
    return "\n".join(parts)
