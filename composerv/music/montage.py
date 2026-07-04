"""Assemble a montage whose cuts land on musical beats.

Given candidate fragments and a track's beat grid, lay shots so every cut falls on a beat.
The feeling sets the pacing (how many beats each shot holds): upbeat = fast cuts, sad = long
holds. Pure -> testable; the beat grid comes from `music.beat` (librosa).
"""

from __future__ import annotations

import os
import statistics
from collections.abc import Callable, Sequence

from pydantic import BaseModel, Field

from composerv.models import IntentionList, MusicBed, MusicIntent, Segment


def salient_in_point(
    motion: Sequence[tuple[float, float]],
    shot_len: float,
    duration: float,
    available: float | None = None,
) -> float:
    """Pick the start of the shot_len window with the MOST motion, so a shot opens on the
    active part of a clip instead of always at the head. motion = [(t, score)]. Clamped so
    [in, in+shot_len] stays in bounds; no/flat signal falls back to 0.0 (the head)."""
    limit = duration if available is None else min(duration, available)
    hi = max(0.0, limit - shot_len)
    if not motion or hi <= 0:
        return 0.0
    best_t, best = 0.0, -1.0
    for t0, _score in motion:
        start = min(max(0.0, float(t0)), hi)
        total = sum(s for t, s in motion if start <= t < start + shot_len)
        if total > best:
            best, best_t = total, start
    return best_t


def _default_motion(proxy: str, _dur: float) -> list[tuple[float, float]]:
    """Real motion profile from the proxy; empty (-> head in-point) if it can't be computed."""
    if not proxy or not os.path.exists(proxy):
        return []
    try:
        from composerv.clarity.sampling import motion_profile

        return motion_profile(proxy)
    except Exception:
        return []


def _default_speech(path: str) -> list[tuple]:
    """Sentences spoken in a clip's audio: a cheap VAD gate (skip silent/wordless clips fast),
    then PRECISE MLX-Whisper sentences as (start, end, text). If Whisper isn't installed (the
    `transcribe` extra), falls back to the coarser VAD windows (start, end) with no text, so
    truncation is still avoided (and the key-line picker just features the longest). Empty if
    no file/audio/voice."""
    if not path or not os.path.exists(path):
        return []
    try:
        from composerv.audio.vad import detect_speech

        vad = detect_speech(path)
    except Exception:
        return []
    if not vad:
        return []
    from composerv.audio.transcribe import transcribe_sentences

    sents = transcribe_sentences(path)  # [(start, end, text)]; [] if the extra isn't installed
    return sents if sents else vad

# feeling -> beats per shot (pacing). Lower = faster cutting.
_PACING = {
    "upbeat": 2, "happy": 2, "playful": 2,
    "calm": 4, "peaceful": 4, "warm": 4, "tender": 4, "nostalgic": 4,
    "sad": 8, "melancholy": 8, "somber": 8,
}


def beats_per_cut_for_feeling(feeling: str, default: int = 4) -> int:
    return _PACING.get((feeling or "").strip().lower(), default)


# feeling -> target shot duration in SECONDS. Pacing by time (not a fixed beat count) makes
# the reel robust to librosa's octave tempo errors: a doubled tempo just yields more
# beats-per-cut, so wall-clock shot length stays put while cuts still land on beats.
_TARGET_SHOT_S = {
    "upbeat": 1.6, "happy": 1.6, "playful": 1.6,
    "calm": 3.0, "peaceful": 3.0, "warm": 3.0, "tender": 3.0, "nostalgic": 3.0,
    "sad": 4.5, "melancholy": 4.5, "somber": 4.5,
}


def target_shot_s_for_feeling(feeling: str, default: float = 3.0) -> float:
    return _TARGET_SHOT_S.get((feeling or "").strip().lower(), default)


def assemble_to_beats(
    fragments: Sequence[tuple[str, float, float]],
    beat_times: Sequence[float],
    beats_per_cut: int = 4,
    max_shot_s: float = 8.0,
    max_shots: int | None = None,
) -> IntentionList:
    """fragments = [(source_id, in_sec, available_sec)]. Returns an IntentionList of shots,
    each lasting one beat-interval (so cuts fall on beats), cycling through the fragments.
    max_shots caps the shot count (None = fill the whole beat grid, the original behavior)."""
    beats_per_cut = max(1, beats_per_cut)
    cuts = list(beat_times)[::beats_per_cut]
    segments: list[Segment] = []
    if not fragments or len(cuts) < 2:
        return IntentionList(story_id="montage", segments=segments)
    fi = 0
    for k in range(len(cuts) - 1):
        if max_shots is not None and len(segments) >= max_shots:
            break
        shot_len = cuts[k + 1] - cuts[k]
        if shot_len <= 0:
            continue
        shot_len = min(shot_len, max_shot_s)
        src, in_sec, avail = fragments[fi % len(fragments)]
        if avail and avail > 0:
            shot_len = min(shot_len, avail)
        segments.append(Segment(kind="clip", source_id=src, in_sec=in_sec,
                                out_sec=in_sec + shot_len, duration_s=shot_len))
        fi += 1
    return IntentionList(story_id="montage", segments=segments)


class MontagePlan(BaseModel):
    feeling: str = ""
    track: str | None = None        # suggested music file
    tempo: float = 0.0
    label: str = ""                 # which day/part this reel is, for titling
    intention: IntentionList
    # selection rationale (spec §4): recorded for audit, not consumed by render/preview:
    intent: MusicIntent | None = None        # the director's request
    match_score: float = 0.0                 # total weighted score of the chosen track
    match_breakdown: dict = Field(default_factory=dict)   # per-axis scores
    beat_snaps: list = Field(default_factory=list)        # [(orig_t, snapped_t, beat_idx)]
    library_gap: bool = False                # True = nothing cleared threshold, chose least-bad


def build_montage(
    store,
    scope_paths: list[str],
    *,
    music_dir: str,
    feeling: str | None = None,
    run: Callable[[str], str] | None = None,        # mood inference + worth judging (LLM)
    beat_fn: Callable[[str], tuple] | None = None,  # detect_beats (librosa)
    motion_fn: Callable[[str, float], list] | None = None,  # per-clip motion profile
    vad_fn: Callable[[str], list] | None = None,    # per-clip sentences (source seconds [+ text])
    select_fn: Callable[..., list] | None = None,   # judge worth -> the worthy span to keep whole
    max_shot_s: float = 8.0,                        # cap for a VISUAL (no-speech) shot
    max_part_s: float = 300.0,                      # one reel runs at most this long (5 min)
    repeat: int = 1,
) -> list[MontagePlan]:
    """Tie the pieces: infer the feeling, suggest a track, detect its beats, and assemble the
    scope's clips in CHRONOLOGICAL order. A talky clip whose conversation the LLM judges worth
    remembering is kept WHOLE (a long hold, ducking the music for its length); other clips are
    short beat-synced visual shots with the music in front. The result is split into parts so no
    part exceeds max_part_s (a new part per day). Returns one MontagePlan per part."""
    from composerv.music.beat import detect_beats
    from composerv.music.library import load_library, suggest_track
    from composerv.music.mood import infer_feeling

    summaries = [store.get_clip_summary(p) for p in scope_paths]
    feeling = feeling or infer_feeling([s for s in summaries if s], run=run)
    track = suggest_track(feeling, load_library(music_dir))
    from composerv.audio.keyline import select_memorable_span
    from composerv.index.when import capture_label
    from composerv.music.highlights import extend_for_speech, project_highlights
    from composerv.music.parts import split_by_day

    beat_fn = beat_fn or detect_beats
    motion_fn = motion_fn or _default_motion
    vad_fn = vad_fn or _default_speech  # precise sentences (VAD-gated MLX-Whisper) [+ text]
    select_fn = select_fn or select_memorable_span  # LLM judges what's worth keeping whole
    tempo, beats = beat_fn(track) if track else (0.0, [])

    # pace by TIME: derive beats_per_cut from a target shot duration and the actual beat gap,
    # so a mis-detected (doubled) tempo doesn't make the cuts twice as fast.
    target = target_shot_s_for_feeling(feeling)
    gaps = [beats[i + 1] - beats[i] for i in range(len(beats) - 1)]
    beat_gap = statistics.median(gaps) if gaps else 0.5
    beats_per_cut = max(1, round(target / beat_gap)) if beat_gap > 0 else 1
    shot_len_est = min(max_shot_s, target)

    ordered = sorted(scope_paths, key=lambda p: (store.get_asset(p).capture_time
                                                 if store.get_asset(p) else "") or "")
    frags = []
    speech_by_source: dict[str, list] = {}
    for p in ordered:
        a = store.get_asset(p)
        if not a:
            continue
        sentences = vad_fn(a.proxy_path or a.path or "") or []
        worthy = select_fn(sentences, run=run) if sentences else []
        if worthy:
            # a worthwhile conversation: keep the WHOLE exchange (open on its start, available =
            # the span so the shot is exactly it), and duck the music for its whole length.
            span_start, span_end = float(worthy[0][0]), float(worthy[-1][1])
            speech_by_source[p] = [(span_start, span_end)]
            frags.append((p, span_start, span_end - span_start))
        else:
            # silent or not-worth-keeping talk: a short visual shot, music in front.
            speech_by_source[p] = []
            in_pt = salient_in_point(motion_fn(a.proxy_path or "", a.duration_s),
                                     shot_len_est, a.duration_s)
            frags.append((p, in_pt, a.duration_s - in_pt))
    il = assemble_to_beats(frags, beats, beats_per_cut=beats_per_cut,
                           max_shot_s=max_shot_s, max_shots=len(frags) * max(1, repeat))
    # grow a talky shot to hold its whole worthy conversation; bounded by the part budget so a
    # mis-judged long span can't break the 5-min split.
    il = extend_for_speech(il, speech_by_source, max_shot_s=max_part_s)

    def day_of(sid: str) -> str:
        a = store.get_asset(sid)
        return capture_label(a.capture_time).split(" ")[0] if a else ""

    parts = split_by_day(il, day_of, max_part_s=max_part_s) or [("", il)]
    plans = []
    for label, il_part in parts:
        if track:
            il_part.music = MusicBed(path=track)  # ride the contract so preview + FCPXML mux it
            # the worthy-conversation spans duck the music + foreground the voice (per part)
            il_part.music.highlights = project_highlights(il_part, speech_by_source)
        plans.append(MontagePlan(feeling=feeling, track=track, tempo=tempo,
                                 label=label, intention=il_part))
    return plans
