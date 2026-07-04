"""Assemble a montage by letting the director (Claude) edit from the footage table.

The ③-driven montage path that replaces the old hard-coded brain in `music.montage.build_montage`.
By default it reads the CACHED perception index from the store (visual moments + transcript,
populated once by `clarity.analyze`), so a director run is fast + quiet — no live VLM. Build the
footage table → director LLM → resolve ids → map to IntentionList → split by day. visual_fn /
vad_fn override the store (for tests or a store-less run); the LLM call is injectable too.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from composerv.director.plan import edit_to_intention, resolve_segments, split_edit_by_day
from composerv.director.prompt import build_director_prompt, parse_edit
from composerv.director.table import build_footage_table


# the director reads the whole footage table and curates in one call; a large scope (many clips +
# photos) legitimately takes several minutes, so give it generous headroom (a too-short timeout
# returns "" and looks like an empty edit). It still usually returns in a few minutes.
DIRECTOR_TIMEOUT_S = 1800
DIRECTOR_MODEL = "claude-opus-4-6"   # the editorial judgment runs on Opus 4.6 (user preference)


def _default_director(prompt: str) -> str:
    from composerv.analyze.backends.claude_cli import claude_text

    return claude_text(prompt, model=DIRECTOR_MODEL, timeout=DIRECTOR_TIMEOUT_S)


def _choose_track(intent, features_lib, library, feeling, suggest_track, rank_tracks, is_gap):
    """Pick a track for the intent. Returns (path|None, score, breakdown, library_gap).
    Falls back to suggest_track when there are no features (spec §12)."""
    if not features_lib:
        return suggest_track(feeling, library), 0.0, {}, False  # no-features fallback
    ranked = rank_tracks(intent, features_lib)
    if not ranked:
        return suggest_track(feeling, library), 0.0, {}, True
    score, tf, breakdown = ranked[0]
    return tf.path, score, breakdown, is_gap(ranked)


def _beats_for(track_path, features_lib, beat_fn):
    """Reuse the chosen track's cached beat_times if present in the feature lib; else detect."""
    for tf in features_lib:
        if tf.path == track_path and tf.beat_times:
            return tf.tempo_bpm, tf.beat_times
    return beat_fn(track_path)


def build_director_montage(
    store,
    scope_paths: list[str],
    *,
    music_dir: str,
    feeling: str | None = None,
    run: Callable[[str], str] | None = None,        # mood inference (LLM)
    beat_fn: Callable[[str], tuple] | None = None,  # detect_beats
    visual_fn: Callable[[str, float], list] | None = None,   # per-clip visual moments
    vad_fn: Callable[[str], list] | None = None,    # per-clip speech sentences
    director_fn: Callable[[str], str] | None = None,  # the director LLM (prompt -> reply text)
    budget_s: float = 120.0,
    max_part_s: float = 300.0,
    sensitive: list[str] | None = None,
    brief=None,
) -> list:
    """Direct the scope into memory-reel parts. Returns a list of MontagePlan (one per day).
    Raises RuntimeError if the director returns nothing usable."""
    from composerv.index.when import capture_label
    from composerv.models import MusicIntent
    from composerv.music.beat import detect_beats
    from composerv.music.beatsnap import beat_snap_segments
    from composerv.music.library import load_features_lib, load_library, suggest_track
    from composerv.music.montage import MontagePlan
    from composerv.music.mood import infer_feeling
    from composerv.music.score import library_gap as _is_gap
    from composerv.music.score import rank_tracks

    director_fn = director_fn or _default_director
    beat_fn = beat_fn or detect_beats

    ordered = sorted(scope_paths, key=lambda p: (store.get_asset(p).capture_time
                                                 if store.get_asset(p) else "") or "")
    summaries = [store.get_clip_summary(p) for p in ordered]
    feeling = feeling or infer_feeling([s for s in summaries if s], run=run)
    library = load_library(music_dir)
    features_lib = load_features_lib(library)

    # Give the director SHORT stable ids (the basename stem), not long paths it would abbreviate
    # and mis-map. id_map translates the chosen ids back to real source paths.
    id_map: dict[str, str] = {}
    rows = []
    for p in ordered:
        a = store.get_asset(p)
        if not a:
            continue
        sid = os.path.splitext(os.path.basename(p))[0] or p
        while sid in id_map:
            sid = f"{sid}_{len(id_map)}"
        id_map[sid] = p
        proxy = a.proxy_path or a.path or ""
        # default: read the cached index from the store; visual_fn/vad_fn override it.
        # the rich getter carries OCR (place names) into the table; boxes stay in the store.
        if visual_fn:
            visual = visual_fn(proxy, a.duration_s) or []
            best_t = None
        else:
            aes = store.get_clip_aesthetics(p)
            best_t = aes.best_t if aes else None
            # A: per-moment quality tags are NOT put in the table. The blind eval showed image
            # quality culling emotionally-valuable shots, so quality must not drive curation — it
            # informs ONLY the in-point, via the per-clip `best ~Xs` header (best_t). boxes stay in
            # the store for reframe; the table ignores v[3].
            visual = [(m.t, m.text, m.ocr, m.objects)
                      for m in store.get_clip_moments_rich(p)]
        raw_speech = (vad_fn(proxy) if vad_fn else store.get_transcript(p)) or []
        speech = [(float(w[0]), float(w[1]), w[2] if len(w) > 2 else "") for w in raw_speech]
        rows.append({
            "clip_id": sid,
            "people": store.clip_person_labels(p),
            "note": "",
            "duration": a.duration_s,
            "photo": a.kind == "photo",
            "visual": visual,
            "best_t": best_t,
            "speech": speech,
        })

    if not visual_fn and not vad_fn and not any(r["visual"] or r["speech"] for r in rows):
        print("[director] perception index is empty — run `composerv analyze <scope>` first "
              "(otherwise the director only sees clip names, no moments/dialogue)", file=sys.stderr)

    table = build_footage_table(rows)
    bc = brief.context if brief else ""
    bs = brief.style if brief else ""
    prompt = build_director_prompt(table, feeling=feeling, budget_s=budget_s, sensitive=sensitive,
                                   brief_context=bc, brief_style=bs)
    parsed = parse_edit(director_fn(prompt))
    base_intent = MusicIntent(**parsed["music_intent"])
    parsed_segments = parsed.get("segments", [])
    resolved = resolve_segments(parsed_segments, list(id_map))

    # translate id -> real path, clamp to the clip's real bounds (the director only saw the
    # described table, so out_s can run past the clip and would crash the render)
    final = []
    for s in resolved:
        full = id_map.get(s["clip_id"])
        if not full:
            continue
        a = store.get_asset(full)
        if a and a.kind == "photo":
            # a still has no source timeline: out_s is the chosen HOLD, clamp it to a sane range
            hold = float(s["out_s"]) - float(s["in_s"])
            hold = 3.0 if hold <= 0 else max(1.0, min(8.0, hold))
            final.append({**s, "clip_id": full, "kind": "photo", "in_s": 0.0, "out_s": hold})
            continue
        in_s = max(0.0, float(s["in_s"]))
        out_s = float(s["out_s"])
        if a and a.duration_s:
            out_s = min(out_s, a.duration_s)
        if out_s - in_s <= 0:
            continue
        final.append({**s, "clip_id": full, "in_s": in_s, "out_s": out_s})
    if len(final) < len(parsed_segments):
        print(f"[director] kept {len(final)}/{len(parsed_segments)} segments "
              f"({len(parsed_segments) - len(final)} dropped or clamped)", file=sys.stderr)
    if not final:
        raise RuntimeError("director returned no usable edit (empty or unparseable reply — "
                           "a large scope can time out the director; check stderr for TimeoutExpired)")
    edit = {"segments": final}

    def day_of(sid: str) -> str:
        a = store.get_asset(sid)
        return capture_label(a.capture_time).split(" ")[0] if a else ""

    plans = []
    for label, segs in split_edit_by_day(edit["segments"], day_of, max_part_s=max_part_s):
        # per-day target_duration_s = sum of this day's timeline lengths (spec per-reel scope)
        day_len = round(sum(float(s["out_s"]) - float(s["in_s"])
                            for s in segs if float(s["out_s"]) > float(s["in_s"])), 3)
        intent = base_intent.model_copy(update={"target_duration_s": day_len})
        chosen, score, breakdown, gap = _choose_track(
            intent, features_lib, library, feeling, suggest_track, rank_tracks, _is_gap)
        il = edit_to_intention({"segments": segs}, fps=30, track=chosen, music_intent=intent)
        beat_snaps: list = []
        _tempo = 0.0
        if chosen:
            _tempo, beat_times = _beats_for(chosen, features_lib, beat_fn)
            asset_durs = {sid: (store.get_asset(sid).duration_s if store.get_asset(sid) else 0.0)
                          for sid in {s.source_id for s in il.segments if s.source_id}}
            il, beat_snaps = beat_snap_segments(il, beat_times, fps=30,
                                                asset_durations=asset_durs)
        plans.append(MontagePlan(
            feeling=feeling, track=chosen, tempo=_tempo, label=label, intention=il,
            intent=intent, match_score=score, match_breakdown=breakdown,
            beat_snaps=beat_snaps, library_gap=gap))
    return plans
