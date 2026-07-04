"""Deterministic, non-LLM track scoring (spec §9). Picks the best-matching track for a
director's MusicIntent. This is the function that ships AND the one the design-time scorer
subagent calls: the LLM only authors the why-match/why-not prose over these numbers.
"""

from __future__ import annotations

from composerv.models import MusicIntent, TrackFeatures

# spec D4 weights (tuned from the 2026-06-26 design-time negotiation; shape raised from 0.5 to 0.6,
# other four scaled down proportionally so the sum remains 1.0).
DEFAULT_WEIGHTS = {"shape": 0.6, "tempo": 0.15, "valence": 0.12, "mode": 0.08, "duration": 0.05}
ACCEPT_THRESHOLD = 0.6  # below this for the top candidate => library_gap (spec §7)
SHAPE_FLOOR = 0.85  # soft gate: tracks below this are pulled down steeply (squared ramp)


def _shape_score(a: list[float], b: list[float]) -> float:
    """1 - mean absolute difference of two 16-point 0..1 curves; 0.0 if either is empty or the
    lengths differ. An empty curve is the flat-track sentinel (features._resample_curve) -> a
    silent/featureless track is a non-match, not a deceptive mid-range hit (spec D1)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    mad = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
    return max(0.0, 1.0 - mad)


def _tempo_score(tempo_bpm: float, lo: float, hi: float) -> float:
    """1.0 inside the band; falls off with distance outside it. Unconstrained band (0,0) returns
    1.0 — full credit so the axis does not drag the total, consistent with _mode_score('any');
    spec §9 '不计此项'."""
    if lo <= 0.0 and hi <= 0.0:
        return 1.0
    if lo <= tempo_bpm <= hi:
        return 1.0
    dist = (lo - tempo_bpm) if tempo_bpm < lo else (tempo_bpm - hi)
    return max(0.0, 1.0 - dist / 60.0)  # 60 bpm out-of-band -> 0


def _mode_score(track_mode: str, pref: str) -> float:
    """1.0 when the preference is 'any' or matches; 0.0 on an explicit mismatch; 0.5 when the
    track mode is 'unknown' (couldn't be estimated): neutral, not penalized (spec D3)."""
    if pref == "any":
        return 1.0
    if track_mode == "unknown":
        return 0.5
    return 1.0 if track_mode == pref else 0.0


def _valence_score(track_v: float, intent_v: float) -> float:
    return max(0.0, 1.0 - abs(track_v - intent_v))


def _duration_score(track_dur: float, target: float) -> float:
    if target <= 0.0:
        return 1.0
    if track_dur >= target:
        return 1.0
    return max(0.0, track_dur / target)


def rank_tracks(
    intent: MusicIntent,
    features_lib: list[TrackFeatures],
    *,
    weights: dict | None = None,
) -> list[tuple[float, TrackFeatures, dict]]:
    """Score every track against the intent; return (total, features, breakdown) sorted high to
    low. breakdown holds the per-axis 0..1 scores (shape/tempo/mode/valence/duration)."""
    w = weights or DEFAULT_WEIGHTS
    out: list[tuple[float, TrackFeatures, dict]] = []
    for tf in features_lib:
        bd = {
            "shape": _shape_score(intent.energy_curve, tf.energy_curve),
            "tempo": _tempo_score(tf.tempo_bpm, intent.tempo_lo, intent.tempo_hi),
            "mode": _mode_score(tf.mode, intent.mode_pref),
            "valence": _valence_score(tf.valence, intent.valence),
            "duration": _duration_score(tf.duration_s, intent.target_duration_s),
        }
        weighted = sum(w[k] * bd[k] for k in bd)
        shape_gate = 1.0 if bd["shape"] >= SHAPE_FLOOR else (bd["shape"] / SHAPE_FLOOR) ** 2
        total = round(weighted * shape_gate, 4)
        bd["shape_gate"] = round(shape_gate, 4)
        out.append((total, tf, bd))
    out.sort(key=lambda r: r[0], reverse=True)
    return out


def library_gap(ranked: list[tuple[float, TrackFeatures, dict]],
                threshold: float = ACCEPT_THRESHOLD) -> bool:
    """True when there is no candidate, or the best one doesn't clear the accept threshold
    (spec §7 tie-break: pick least-bad, flag the gap, don't crash)."""
    return not ranked or ranked[0][0] < threshold
