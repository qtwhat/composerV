"""Render the clip-clarity catalog: a static HTML page the user opens to see the whole
archive at a glance (keyframes + 'what is this' + key facts), grouped by day.

`render_catalog` is a pure function (cards -> HTML string), so it is easy to test.
`build_cards` reads the store (assets + clarity + keyframes) into cards.
"""

from __future__ import annotations

import os
from collections import defaultdict
from html import escape

from pydantic import BaseModel

from composerv.store.db import Store

_STYLE = """<style>
 body{font-family:-apple-system,system-ui,sans-serif;margin:24px;background:#111;color:#eee}
 h1{font-size:20px} h2{font-size:15px;color:#9cf;border-bottom:1px solid #333;padding-top:18px}
 .card{display:flex;gap:12px;padding:10px;margin:8px 0;background:#1b1b1b;border-radius:8px}
 .card.sel{outline:2px solid #4c8}
 .kf img{height:84px;border-radius:4px;margin-right:4px}
 .desc{flex:1;font-size:14px;line-height:1.4}
 .empty{color:#888;font-style:italic}
 .facts{font-size:12px;color:#aaa;margin-top:6px}
 .facts b{color:#ddd} .sel{color:#4c8;font-weight:600;margin-left:6px}
 .src{color:#c9a}
</style>"""


class ClarityCard(BaseModel):
    clip_id: str                       # short id (basename) used by CLI select/refine
    path: str                          # full source path
    summary: str = ""
    source: str = ""                   # "" | "local" | "claude"
    selected: bool = False
    duration_s: float = 0.0
    capture_time: str | None = None
    keyframes: list[str] = []          # thumbnail paths, in time order
    people: list[str] = []             # named people present in the clip


def _render_card(c: ClarityCard) -> str:
    imgs = "".join(f'<img src="{escape(p)}">' for p in c.keyframes)
    desc = escape(c.summary) if c.summary else "<span class='empty'>no description yet</span>"
    dur = f"{c.duration_s:.0f}s" if c.duration_s else ""
    when = escape(c.capture_time or "")
    src = f" · <span class='src'>{escape(c.source)}</span>" if c.source else ""
    badge = "<span class='sel'>✓ selected</span>" if c.selected else ""
    people = f"<div class='people'>👤 {escape(', '.join(c.people))}</div>" if c.people else ""
    cls = "card sel" if c.selected else "card"
    return (
        f"<div class='{cls}' data-id='{escape(c.clip_id)}'>"
        f"<div class='kf'>{imgs}</div>"
        f"<div class='desc'>{desc}{people}"
        f"<div class='facts'><b>{escape(c.clip_id)}</b> · {dur} · {when}{src}{badge}</div>"
        f"</div></div>"
    )


def render_catalog(cards: list[ClarityCard], title: str = "composerV catalog") -> str:
    groups: dict[str, list[ClarityCard]] = defaultdict(list)
    for c in cards:
        day = (c.capture_time or "")[:10] or "unknown"
        groups[day].append(c)
    days = sorted(groups)
    dated = [d for d in days if d != "unknown"]
    span = f"{dated[0]} … {dated[-1]}" if dated else "no dates"
    overview = f"{len(cards)} clips across {len(days)} days · {escape(span)}"
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>{escape(title)}</title>", _STYLE, "</head><body>",
        f"<h1>{escape(title)}</h1>",
        f"<p class='overview'>{overview}</p>",
    ]
    for day in days:
        parts.append(f"<h2>{escape(day)} ({len(groups[day])})</h2>")
        for c in sorted(groups[day], key=lambda c: (c.capture_time or "", c.clip_id)):
            parts.append(_render_card(c))
    parts.append("</body></html>")
    return "\n".join(parts)


def build_cards(store: Store) -> list[ClarityCard]:
    """Assemble cards from the store (assets + clarity record + display keyframes)."""
    cards: list[ClarityCard] = []
    for mi in store.list_assets():
        rec = store.get_clarity(mi.path)
        kfs = [p for _t, p in store.get_keyframes(mi.path)]
        cards.append(ClarityCard(
            clip_id=os.path.basename(mi.path), path=mi.path,
            summary=rec.summary, source=rec.source, selected=rec.selected,
            duration_s=mi.duration_s, capture_time=mi.capture_time, keyframes=kfs,
            people=store.clip_person_names(mi.path),
        ))
    return cards
