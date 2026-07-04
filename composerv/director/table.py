"""Render the unified, timestamped footage table the director reads.

One block per clip: a header (id, who, optional human note) then visual + speech rows merged in
time order. Pure: the caller supplies already-extracted per-clip data (VLM moments, Whisper
sentences, people, note), so this is trivially testable and free of model calls.
"""

from __future__ import annotations

from collections.abc import Sequence


# at the same timestamp: speech, then the visual moment, then that frame's on-screen text
_ROW_ORDER = {"speech": 0, "visual": 1, "text": 2}


def build_footage_table(clips: Sequence[dict]) -> str:
    """clips: [{clip_id, people:[str], note:str, visual:[(t, text[, ocr[, objects]])],
    speech:[(t0, t1, text)]}]. Returns the text table; visual/OCR/speech rows interleave by time.
    Visual tuples may carry OCR (rendered as 'on screen: …') and grounded objects (ignored here —
    boxes are for reframe, not director prose)."""
    blocks: list[str] = []
    for c in clips:
        who = ", ".join(c.get("people") or []) or "—"
        is_photo = bool(c.get("photo"))
        head = f"[{'photo' if is_photo else 'clip'} {c['clip_id']}]  who: {who}"
        if is_photo:
            head += "   (still photo — pick a 2-5s hold via out_s, and motion: static/in/out)"
        elif c.get("duration"):
            head += f"   len: {float(c['duration']):.0f}s"
        if c.get("note"):
            head += f"   note: {c['note']}"
        best_t = c.get("best_t")
        if best_t is not None:
            head += f"   best ~{float(best_t):.1f}s"
        rows = []
        for v in c.get("visual", []):
            t, txt = float(v[0]), v[1]
            qtag = v[4] if len(v) > 4 else ""           # v[3]=objects stays ignored (boxes -> reframe)
            rows.append(("visual", t, t, f"{txt}  {qtag}" if qtag else txt))
            ocr = v[2] if len(v) > 2 else ""
            if ocr:
                rows.append(("text", t, t, f"on screen: {ocr}"))
        rows += [("speech", float(a), float(b), txt) for a, b, txt in c.get("speech", [])]
        rows.sort(key=lambda r: (r[1], _ROW_ORDER.get(r[0], 1)))
        lines = [head]
        for kind, a, b, txt in rows:
            ts = f"t={a:.1f}-{b:.1f}s" if kind == "speech" else f"t={a:.1f}s"
            lines.append(f"  {kind}  {ts}  {txt}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
