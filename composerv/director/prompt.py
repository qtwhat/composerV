"""Build the director prompt and parse its reply.

The prompt encodes every editorial requirement gathered so far (human-led, emotional curation,
keep conversations whole, visual + audio count equally, music-as-bed, pace by feeling,
chronological + smooth, salient in-points, varied rhythm, fit the budget by omission) and the
two-step rule: reason in prose first, THEN emit the structured edit. build_director_prompt /
parse_edit are pure; the live Claude call is injected by the caller.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

_PREAMBLE = (
    "You are the editor of a personal memory reel — a short, music-backed montage cut from one "
    "family's home footage, the kind of video someone keeps to relive a day. You do NOT watch "
    "the video; you read it as a timeline of descriptions. Decide the edit: which moments to "
    "keep, in what order, how long each runs, and where the music should step back for "
    "someone's voice. A human will review and may adjust your cut, so give a short reason for "
    "each choice.\n\nINPUT\n"
)

_RULES = """
HOW TO EDIT (in priority order)
1. Human-led. If a clip carries a human note marking importance or a highlighted span, honour
   it above your own judgement — keep it, and keep what they pointed at.
2. Curate by emotion, not by counting. Keep what is worth remembering — warmth, humour, a
   milestone, a striking sight, something said that matters. Drop filler: logistics, "are you
   filming me", ambient noise, repetitive nothing. Judge this by emotion and memory value, NOT by
   image quality — a treasured moment that is a little soft still belongs; a sharp shot of nothing
   does not. (Quality tags inform the in-point only; see rule 8.)
3. Two dimensions count equally. A moment earns its place by what is SAID (a real exchange) OR
   what is SEEN (a child's first try, a view, a shared activity). Do not keep only talking clips.
4. Keep a worthy conversation WHOLE. If an exchange is worth remembering, keep it complete —
   never cut a person off mid-sentence, and never reduce a conversation to a single line. There
   is no "best line"; keep the moment.
5. Music is the bed. The reel is set to music; keep it in front by default. Only ask to duck it
   (duck_music=true) during a conversation or sound you are deliberately featuring. Do not duck
   the whole reel.
6. Pace by feeling. A visual moment is a short shot at about the feeling's shot length and
   should fall on a beat. A kept conversation is a long hold — let it play; off the beat is fine.
7. Keep chronological order so the day can be relived in sequence. Within that, prefer a smooth
   visual flow (don't jam two jarringly different shots back to back) when the order has slack.
8. Open a visual shot on its active / meaningful instant, not always the clip's start. Some clips
   show a header "best ~Xs" — the sharpest, best-composed instant. Prefer opening a shot at or
   near that best moment. (Image quality informs only this in-point, never whether to keep a clip.)
9. Vary the rhythm — mix long holds with quick beats; don't make every shot the same.
10. Fit the length budget by LEAVING THINGS OUT, never by trimming a worthy moment short.
11. Photos are stills (marked [photo …], no sound). Use a good one like a held beat: set in_s=0
    and out_s to a 2-5s hold, kind="photo", and choose motion — "in" (slow push), "out" (slow
    pull) to give it life, or "static". Place it in chronological order with the videos.

PROCESS — two steps, in this order:
A. Think in prose first: what is this footage about, which moments matter and why, the
   emotional arc, what to drop.
B. THEN output the structured edit as a single JSON object, after your reasoning:
{
  "feeling": "<the mood you are cutting to>",
  "arc": "<one or two sentences: the through-line of this reel>",
  "energy_curve": [<16 numbers 0..1: the desired energy/loudness arc from open (point 0) to
                   close (point 15). e.g. a quiet intro then a build to a peak then a settle:
                   0.2,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.8,0.7,0.6,0.5,0.4,0.4,0.3,0.3>],
  "tempo_lo": <preferred minimum tempo, or 0 for no preference>,
  "tempo_hi": <preferred maximum tempo, or 0 for no preference>,
  "mode_pref": "major | minor | any",
  "valence": <0..1, how bright/positive the music should feel>,
  "target_duration_s": <how long the music should run, in seconds>,
  "segments": [
    {"clip_id": "<id from the table>", "in_s": 0.0, "out_s": 0.0,
     "kind": "moment | conversation | photo", "motion": "static | in | out (photos only)",
     "duck_music": false, "reason": "<one line>"}
  ]
}
clip_id must be EXACTLY the id shown in [clip <id>] (copy it verbatim, do not shorten it);
in_s/out_s are seconds within that clip, between 0 and its stated len; segments are in final
reel order. The energy_curve is YOUR call: imagine the emotional rise and fall of this reel
over its length, then express it as exactly 16 numbers between 0 (quiet) and 1 (loud). The
music will be chosen to match that shape: describe what you want, not a specific track.
"""


def build_director_prompt(
    table_text: str,
    *,
    feeling: str,
    budget_s: float = 300.0,
    sensitive: Sequence[str] | None = None,
    brief_context: str = "",
    brief_style: str = "",
) -> str:
    sens = ", ".join(sensitive) if sensitive else "none"
    brief = ""
    if brief_context or brief_style:
        brief = (
            "HUMAN BRIEF (user input, HIGHEST priority — follow it over your own judgement):\n"
            f"- Context: {brief_context or '(none)'}\n"
            f"- Style / pacing: {brief_style or '(none)'}\n\n"
        )
    header = (
        f"- Feeling: {feeling}  (pace: upbeat = quick cuts, calm/nostalgic = longer holds, "
        "sad = lingering)\n"
        "- Music: chosen AFTER your edit to match the energy arc you describe. Cut to the shot "
        "lengths the feeling implies; cuts are aligned to the music's beats afterward.\n"
        f"- Target length: about {budget_s:.0f}s: be selective; leave out weak material to stay "
        "near it.\n"
        f"- Sensitive people (do NOT feature): {sens}\n"
        "- Footage table, chronological. Timestamps are in each clip's OWN seconds. Each clip's "
        "rows over time are: 'visual' (what is seen), 'speech' (what is said), and 'on screen' "
        "(text read off signs/captions: a clue to place or event, useful for the arc):\n\n"
        f"{table_text}\n"
    )
    return _PREAMBLE + brief + header + _RULES


def _clamp_curve(raw, n: int = 16) -> list[float]:
    """Coerce the director's energy_curve to exactly n floats in 0..1; [] if nothing usable."""
    vals: list[float] = []
    for v in raw if isinstance(raw, list) else []:
        try:
            vals.append(min(1.0, max(0.0, float(v))))
        except (TypeError, ValueError):
            continue
    if not vals:
        return []
    if len(vals) == n:
        return vals
    # resample to n points (linear) so a short/long list still matches the track curves
    out = []
    for i in range(n):
        pos = i * (len(vals) - 1) / (n - 1) if n > 1 else 0
        lo = int(pos)
        hi = min(lo + 1, len(vals) - 1)
        frac = pos - lo
        out.append(round(vals[lo] * (1 - frac) + vals[hi] * frac, 4))
    return out


def _extract_intent(obj: dict) -> dict:
    """Pull the director's music preferences from the parsed edit, tolerant of missing fields.
    arc_text accepts either the 'arc_text' key (design-time prompt) or the runtime 'arc' key."""

    def _f(key, default=0.0):
        try:
            return float(obj.get(key, default))
        except (TypeError, ValueError):
            return default

    pref = obj.get("mode_pref", "any")
    pref = pref if pref in ("major", "minor", "any") else "any"
    arc = obj.get("arc_text") or obj.get("arc") or ""
    return {
        "energy_curve": _clamp_curve(obj.get("energy_curve", [])),
        "tempo_lo": _f("tempo_lo"),
        "tempo_hi": _f("tempo_hi"),
        "mode_pref": pref,
        "valence": _f("valence", 0.5),
        "target_duration_s": _f("target_duration_s"),
        "arc_text": str(arc),
    }


def parse_edit(text: str) -> dict:
    """Pull the JSON edit out of the reply (reasoning prose may precede it). Returns
    {feeling, arc, segments:[{clip_id,in_s,out_s,kind,duck_music,reason}], music_intent:dict};
    segments=[] on failure. Tolerant of code fences / prose around the object."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {"feeling": "", "arc": "", "segments": [], "music_intent": _extract_intent({})}
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {"feeling": "", "arc": "", "segments": [], "music_intent": _extract_intent({})}
    segments = []
    for x in obj.get("segments", []) or []:
        if not isinstance(x, dict):
            continue
        try:
            in_s, out_s = float(x["in_s"]), float(x["out_s"])
        except (KeyError, TypeError, ValueError):
            continue
        if not x.get("clip_id") or out_s <= in_s:
            continue
        kind = x.get("kind", "moment")
        kind = kind if kind in ("conversation", "moment", "photo") else "moment"
        segments.append({
            "clip_id": str(x["clip_id"]),
            "in_s": in_s,
            "out_s": out_s,
            "kind": kind,
            "motion": str(x.get("motion", "")),   # for photo stills: static | in | out
            "duck_music": bool(x.get("duck_music", False)),
            "reason": str(x.get("reason", "")),
        })
    raw = obj.get("segments", []) or []
    if len(segments) < len(raw):
        print(f"[parse_edit] dropped {len(raw) - len(segments)} malformed segment(s) "
              f"(missing clip_id or out<=in)", file=sys.stderr)
    return {"feeling": str(obj.get("feeling", "")), "arc": str(obj.get("arc", "")),
            "segments": segments, "music_intent": _extract_intent(obj)}
