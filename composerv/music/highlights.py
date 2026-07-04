"""Project detected audio-highlight windows (source-clip seconds) onto the timeline.

Detection naturally produces windows in a clip's OWN source seconds (and the same clip may
appear several times in a montage). This pure compile-time adapter maps each source window
onto every timeline occurrence of its clip, clamps it to that segment's trim, then merges
windows whose edge-ramps would overlap and drops windows too short to ramp. The output is a
list of timeline-second AudioHighlight, which is what the single IntentionList contract and
both render paths (preview + FCPXML) consume.

Why frame-accurate cursor accumulation: build_composition lays each segment at an integer
number of frames, so the timeline start of segment N is sum(round(dur_i*fps)/fps), NOT the
raw float sum. Using raw floats would drift window boundaries by milliseconds on clips whose
duration is not frame-aligned.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from composerv.models import AudioHighlight, IntentionList


def extend_for_speech(
    il: IntentionList,
    speech_by_source: Mapping[str, Sequence[tuple[float, float]]],
    max_shot_s: float | None = None,
) -> IntentionList:
    """Grow any shot that would cut through a sentence so it contains the WHOLE sentence:
    if the shot starts inside a speech segment, pull its in-point back to the sentence start;
    if it ends inside one, push its out-point to the sentence end. A sentence already fully
    inside the shot, or not overlapping it, leaves the shot unchanged. Speech windows are in
    the clip's own source seconds (the VAD output). Pure: returns a new IntentionList.

    This is what keeps a person from being cut off mid-word in the reel; the timeline simply
    lengthens for that shot (later cuts drift off the beat, which is the accepted trade)."""
    segs = []
    for s in il.segments:
        if s.kind != "clip" or s.source_id is None or s.in_sec is None or s.out_sec is None:
            segs.append(s)
            continue
        new_in, new_out = s.in_sec, s.out_sec
        for ss, se in speech_by_source.get(s.source_id, []):
            if se > s.in_sec and ss < s.out_sec:  # this sentence overlaps the shot
                new_in = min(new_in, ss)
                new_out = max(new_out, se)
        if max_shot_s is not None:
            new_out = min(new_out, new_in + max_shot_s)
        segs.append(s.model_copy(update={"in_sec": new_in, "out_sec": new_out,
                                         "duration_s": new_out - new_in}))
    return il.model_copy(update={"segments": segs})


def _frame_snap(seconds: float, fps: int) -> float:
    return round(seconds * fps) / fps


def project_highlights(
    il: IntentionList,
    source_windows: Mapping[str, Sequence[tuple]],
    *,
    default_ramp: float = 0.40,
) -> list[AudioHighlight]:
    """source_windows = {source_id: [(start_src, end_src[, label]), ...]}. Returns timeline
    AudioHighlights, clamped to each occurrence's trim, merged on ramp skirts, sub-2-frame
    dropped."""
    fps = il.timeline_fps
    min_len = 2.0 / fps  # a window shorter than two frames cannot carry an in/out ramp
    raw: list[AudioHighlight] = []

    cursor = 0.0
    for seg in il.segments:
        if not seg.enabled:
            continue
        seg_start = cursor
        cursor = _frame_snap(cursor + seg.duration_s, fps)
        if seg.kind != "clip" or seg.source_id is None:
            continue
        in_sec = seg.in_sec or 0.0
        out_sec = seg.out_sec if seg.out_sec is not None else in_sec + seg.duration_s
        for w in source_windows.get(seg.source_id, []):
            ws, we = float(w[0]), float(w[1])
            label = w[2] if len(w) > 2 else ""
            cs, ce = max(ws, in_sec), min(we, out_sec)  # clamp to this occurrence's trim
            if ce - cs < min_len:
                continue
            raw.append(AudioHighlight(
                start_s=seg_start + (cs - in_sec),
                end_s=seg_start + (ce - in_sec),
                ramp_s=default_ramp,
                label=label,
            ))

    return _merge(raw)


def _merge(highlights: list[AudioHighlight]) -> list[AudioHighlight]:
    """Merge windows whose edge-ramp skirts overlap. Overlapping ramp ranges crash the
    AVAudioMix, so a later window starting within 2*ramp of the current window's end is
    folded in. Half-open: windows that merely abut (skirt boundaries touch) are kept apart."""
    if not highlights:
        return []
    ordered = sorted(highlights, key=lambda h: h.start_s)
    out = [ordered[0]]
    for h in ordered[1:]:
        cur = out[-1]
        ramp = max(cur.ramp_s, h.ramp_s)
        if h.start_s < cur.end_s + 2 * ramp:  # skirts would overlap -> merge
            labels = [s for s in (cur.label, h.label) if s]
            out[-1] = AudioHighlight(
                start_s=cur.start_s,
                end_s=max(cur.end_s, h.end_s),
                ramp_s=cur.ramp_s,
                music_duck_db=cur.music_duck_db,
                clip_db=cur.clip_db,
                label="; ".join(labels),
            )
        else:
            out.append(h)
    return out
