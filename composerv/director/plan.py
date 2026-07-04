"""Map the director's decisions back onto the timeline → an IntentionList the render layer eats.

Each decided segment becomes a Segment (source in/out); a segment flagged duck_music projects
an AudioHighlight over its timeline span so the music dips and the clip's own audio lifts for
it. Highlights are merged (adjacent ducks would otherwise overlap-crash the AVAudioMix). Pure.
"""

from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Callable, Sequence

from composerv.models import AudioHighlight, IntentionList, MusicBed, Segment
from composerv.music.highlights import _merge


def split_edit_by_day(
    segments: Sequence[dict],
    day_of: Callable[[str], str],
    max_part_s: float = 300.0,
    min_part_s: float = 30.0,
) -> list[tuple[str, list[dict]]]:
    """Group decided segments into parts: a new part per day and per max_part_s budget. Returns
    [(label, [segs])]; a day that overruns becomes '<day>（1）', '（2）', … (mirrors
    music.parts.split_by_day, but over the director's raw segment dicts so each part can be
    mapped from time 0). A trailing part shorter than min_part_s is merged back into the previous
    part of the SAME day, so a ~5-min reel never orphans a tiny tail (e.g. 292s + 8s -> one reel)."""
    groups: list[list] = []
    cur_day: str | None = None
    cur: list[dict] = []
    cur_len = 0.0
    for s in segments:
        day = day_of(s["clip_id"])
        dur = float(s["out_s"]) - float(s["in_s"])
        if cur and (day != cur_day or cur_len + dur > max_part_s):
            groups.append([cur_day or "", cur])
            cur, cur_len = [], 0.0
        cur_day = day
        cur.append(s)
        cur_len += dur
    if cur:
        groups.append([cur_day or "", cur])

    def _dur(segs: list[dict]) -> float:
        return sum(float(x["out_s"]) - float(x["in_s"]) for x in segs)

    merged: list[list] = []
    for day, segs in groups:
        if merged and merged[-1][0] == day and _dur(segs) < min_part_s:
            merged[-1][1].extend(segs)  # absorb a tiny same-day tail rather than orphan it
        else:
            merged.append([day, segs])
    groups = merged
    per_day = Counter(d for d, _ in groups)
    seen: Counter = Counter()
    out: list[tuple[str, list[dict]]] = []
    for day, segs in groups:
        if per_day[day] > 1:
            seen[day] += 1
            label = f"{day}（{seen[day]}）"
        else:
            label = day
        out.append((label, segs))
    return out


def resolve_segments(segments: Sequence[dict], known_ids: Sequence[str]) -> list[dict]:
    """Map each segment's clip_id back to a real source id. The LLM tends to abbreviate a long
    path to its stem, which then fails to map; match exact first, else the UNIQUE known id that
    contains (or is contained by) it. Segments that match nothing — or ambiguously — are dropped."""
    known = list(known_ids)
    kset = set(known)
    out: list[dict] = []
    for s in segments:
        cid = s.get("clip_id", "")
        if cid in kset:
            out.append(s)
            continue
        cands = [k for k in known if cid and (cid in k or k in cid)]
        if len(cands) == 1:
            out.append({**s, "clip_id": cands[0]})
        elif len(cands) > 1:
            print(f"[resolve_segments] dropped ambiguous clip_id {cid!r} "
                  f"({len(cands)} matches)", file=sys.stderr)
        else:
            print(f"[resolve_segments] dropped unmatched clip_id {cid!r}", file=sys.stderr)
    return out


def edit_to_intention(
    edit: dict,
    *,
    fps: int = 30,
    track: str | None = None,
    music_intent=None,   # carried for symmetry; track selection happens in director.montage
    default_ramp: float = 0.40,
) -> IntentionList:
    """edit = parse_edit(...) output. Returns an IntentionList (segments in reel order) with a
    MusicBed when `track` is given, whose highlights are the duck_music segments on the timeline."""
    segments: list[Segment] = []
    highlights: list[AudioHighlight] = []
    cursor = 0.0
    for s in edit.get("segments", []):
        dur = float(s["out_s"]) - float(s["in_s"])
        if dur <= 0:
            continue
        seg_start = cursor
        cursor = round((cursor + dur) * fps) / fps  # frame-snapped, like the renderer lays it
        is_photo = s.get("kind") == "photo"
        segments.append(Segment(
            kind="photo" if is_photo else "clip", source_id=s["clip_id"], in_sec=float(s["in_s"]),
            out_sec=float(s["out_s"]), duration_s=dur, note=s.get("reason", ""),
            motion=s.get("motion", "") if is_photo else "",
            duck=bool(s.get("duck_music")) and not is_photo,
        ))
        if s.get("duck_music") and not is_photo:  # a still has no audio to duck to
            highlights.append(AudioHighlight(
                start_s=seg_start, end_s=seg_start + dur, ramp_s=default_ramp,
                label=s.get("reason", ""),
            ))
    il = IntentionList(story_id="director", segments=segments)
    if track:
        il.music = MusicBed(path=track)
        il.music.highlights = _merge(highlights)
    return il
