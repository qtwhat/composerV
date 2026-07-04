"""Align an edit's cut points to the chosen track's real beats (spec §8 step 3).

The director cuts to feeling-implied shot lengths without knowing the final track (it is chosen
afterward). This post-step nudges each clip segment's timeline cut to the nearest music beat by
editing out_sec: the field the renderer/preview actually read (composition.py:143-146,
export.py:43, edl.py:24-30 all use out_sec - in_sec; duration_s is ignored for clips). in_sec
(which moment plays) is kept fixed; out_sec is clamped so it never overruns the source clip.
Photos and gaps are left alone. After snapping, duck windows are rebuilt from the snapped layout
so they stay aligned to the segments that own them. Pure: returns a new IntentionList + a snap log.
"""

from __future__ import annotations

import bisect

from composerv.models import AudioHighlight, IntentionList
from composerv.music.highlights import _frame_snap, _merge


def _nearest_beat(t: float, beats: list[float]) -> tuple[float, int]:
    """The beat time closest to t and its index. beats must be sorted."""
    i = bisect.bisect_left(beats, t)
    cands = []
    if i < len(beats):
        cands.append((abs(beats[i] - t), beats[i], i))
    if i > 0:
        cands.append((abs(beats[i - 1] - t), beats[i - 1], i - 1))
    cands.sort()
    return cands[0][1], cands[0][2]


def beat_snap_segments(
    il: IntentionList,
    beat_times: list[float],
    *,
    fps: int = 30,
    max_drift_s: float = 0.5,
    asset_durations: dict[str, float] | None = None,
) -> tuple[IntentionList, list[tuple[float, float, int]]]:
    """Snap each clip segment's timeline cut to the nearest beat (within max_drift_s) by moving
    out_sec. Returns (new IntentionList, [(orig_cut_t, snapped_cut_t, beat_idx)]). Empty beats ->
    no-op. asset_durations[source_id] caps out_sec at the clip's real source length."""
    beats = sorted(float(b) for b in beat_times)
    if not beats:
        return il, []
    durs = asset_durations or {}

    snaps: list[tuple[float, float, int]] = []
    new_segs = []
    cursor = 0.0
    for seg in il.segments:
        if seg.kind != "clip" or seg.source_id is None or not seg.enabled:
            new_segs.append(seg)
            cursor = _frame_snap(cursor + seg.duration_s, fps)
            continue
        orig_cut = _frame_snap(cursor + seg.duration_s, fps)  # timeline end of this shot
        beat_t, beat_idx = _nearest_beat(orig_cut, beats)
        new_dur = round(beat_t - cursor, 6)
        src_cap = durs.get(seg.source_id)
        overruns_source = src_cap is not None and (seg.in_sec + new_dur) > src_cap + 1e-6
        if abs(beat_t - orig_cut) <= max_drift_s and new_dur > 0 and not overruns_source:
            snaps.append((orig_cut, beat_t, beat_idx))
            new_segs.append(seg.model_copy(update={
                "out_sec": round(seg.in_sec + new_dur, 6),  # the field render reads
                "duration_s": new_dur,
            }))
            cursor = _frame_snap(beat_t, fps)
        else:
            new_segs.append(seg)
            cursor = orig_cut

    new_il = il.model_copy(update={"segments": new_segs})
    if new_il.music is not None:
        new_il.music = new_il.music.model_copy(
            update={"highlights": _rebuild_highlights(new_segs, fps,
                                                      default_ramp=_ramp_of(il))})
    return new_il, snaps


def _ramp_of(il: IntentionList) -> float:
    """Reuse the ramp of the existing highlights so rebuilt windows keep the same skirt."""
    if il.music and il.music.highlights:
        return il.music.highlights[0].ramp_s
    return 0.40


def _rebuild_highlights(segments, fps: int, *, default_ramp: float) -> list:
    """Re-walk the snapped segments and place a duck window over each clip segment whose duck flag
    is set, using the segment's NEW timeline span. Mirrors edit_to_intention's projection so the
    windows stay aligned after snapping (spec §8). Merges overlapping ramp skirts."""
    highlights = []
    cursor = 0.0
    for seg in segments:
        dur = seg.duration_s
        start = cursor
        cursor = _frame_snap(cursor + dur, fps)
        if seg.kind == "clip" and getattr(seg, "duck", False) and seg.enabled:
            highlights.append(AudioHighlight(
                start_s=_frame_snap(start, fps), end_s=_frame_snap(start + dur, fps),
                ramp_s=default_ramp, label=seg.note or "",
            ))
    return _merge(highlights)
