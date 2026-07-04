from composerv.models import MusicIntent, TrackFeatures
from composerv.music.score import (ACCEPT_THRESHOLD, DEFAULT_WEIGHTS, SHAPE_FLOOR,
                                    library_gap, rank_tracks)


def _tf(path, **kw):
    base = dict(duration_s=120.0, tempo_bpm=120.0, mode="major", valence=0.6,
                energy_curve=[0.5] * 16)
    base.update(kw)
    return TrackFeatures(path=path, **base)


def test_default_weights_match_spec_d4():
    # tuned in the 2026-06-26 design-time negotiation: shape raised 0.5→0.6, others scaled down
    assert DEFAULT_WEIGHTS == {"shape": 0.6, "tempo": 0.15, "valence": 0.12,
                               "mode": 0.08, "duration": 0.05}
    assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9


def test_rank_prefers_closest_energy_shape():
    rise = [i / 15 for i in range(16)]            # 0..1 ramp up
    fall = [1 - i / 15 for i in range(16)]        # 1..0 ramp down
    intent = MusicIntent(energy_curve=rise)
    lib = [_tf("/m/rise.mp3", energy_curve=rise), _tf("/m/fall.mp3", energy_curve=fall)]
    ranked = rank_tracks(intent, lib)
    assert ranked[0][1].path == "/m/rise.mp3"
    assert ranked[0][2]["shape"] > ranked[1][2]["shape"]


def test_flat_curve_track_does_not_beat_real_shape_match():
    rise = [i / 15 for i in range(16)]
    intent = MusicIntent(energy_curve=rise)
    # a flat/silent track carries energy_curve=[] (Task 2 sentinel) -> shape must be a non-match
    flat = _tf("/m/flat.mp3", energy_curve=[])
    good = _tf("/m/rise.mp3", energy_curve=rise)
    ranked = rank_tracks(intent, [flat, good])
    assert ranked[0][1].path == "/m/rise.mp3"
    flat_shape = next(bd["shape"] for _s, tf, bd in ranked if tf.path == "/m/flat.mp3")
    assert flat_shape == 0.0


def test_tempo_band_zero_is_unconstrained():
    # unconstrained tempo (0,0) -> full credit (1.0), does not drag the total (spec §9 不计此项)
    intent = MusicIntent(energy_curve=[0.5] * 16, tempo_lo=0.0, tempo_hi=0.0)
    lib = [_tf("/m/a.mp3", tempo_bpm=200.0)]
    ranked = rank_tracks(intent, lib)
    assert ranked[0][2]["tempo"] == 1.0


def test_unconstrained_tempo_total_reaches_1_0():
    # With tempo unconstrained the 0.2 weight is not forfeited; a perfect match on all other
    # axes should reach total == 1.0 (not cap at 0.8 as the old 0.0 return caused).
    curve = [float(i) / 15 for i in range(16)]
    intent = MusicIntent(
        energy_curve=curve,
        tempo_lo=0.0,        # unconstrained
        tempo_hi=0.0,
        valence=0.7,
        mode_pref="any",     # mode always 1.0
        target_duration_s=120.0,
    )
    track = _tf("/m/perfect.mp3", energy_curve=curve, valence=0.7, mode="major",
                duration_s=120.0, tempo_bpm=999.0)  # tempo_bpm irrelevant when unconstrained
    ranked = rank_tracks(intent, [track])
    assert ranked[0][0] == 1.0


def test_tempo_in_band_scores_full():
    intent = MusicIntent(energy_curve=[0.5] * 16, tempo_lo=100.0, tempo_hi=140.0)
    inb = rank_tracks(intent, [_tf("/m/in.mp3", tempo_bpm=120.0)])[0][2]["tempo"]
    out = rank_tracks(intent, [_tf("/m/out.mp3", tempo_bpm=180.0)])[0][2]["tempo"]
    assert inb == 1.0 and out < 1.0


def test_mode_any_does_not_penalize():
    intent = MusicIntent(energy_curve=[0.5] * 16, mode_pref="any")
    assert rank_tracks(intent, [_tf("/m/a.mp3", mode="minor")])[0][2]["mode"] == 1.0


def test_mode_mismatch_scores_zero():
    intent = MusicIntent(energy_curve=[0.5] * 16, mode_pref="major")
    assert rank_tracks(intent, [_tf("/m/a.mp3", mode="minor")])[0][2]["mode"] == 0.0


def test_mode_unknown_is_neutral_not_zero():
    # a real track whose mode couldn't be estimated should not be harshly penalized (D3)
    intent = MusicIntent(energy_curve=[0.5] * 16, mode_pref="major")
    assert rank_tracks(intent, [_tf("/m/a.mp3", mode="unknown")])[0][2]["mode"] == 0.5


def test_duration_partial_when_track_too_short():
    intent = MusicIntent(energy_curve=[0.5] * 16, target_duration_s=120.0)
    full = rank_tracks(intent, [_tf("/m/a.mp3", duration_s=120.0)])[0][2]["duration"]
    half = rank_tracks(intent, [_tf("/m/b.mp3", duration_s=60.0)])[0][2]["duration"]
    assert full == 1.0 and abs(half - 0.5) < 1e-6


def test_library_gap_true_when_nothing_clears_threshold():
    intent = MusicIntent(energy_curve=[i / 15 for i in range(16)], mode_pref="minor",
                         tempo_lo=200.0, tempo_hi=210.0, valence=0.0)
    lib = [_tf("/m/bad.mp3", energy_curve=[1 - i / 15 for i in range(16)], mode="major",
               tempo_bpm=80.0, valence=1.0)]
    ranked = rank_tracks(intent, lib)
    assert ranked[0][0] < ACCEPT_THRESHOLD
    assert library_gap(ranked) is True


def test_empty_library_returns_empty_and_is_a_gap():
    ranked = rank_tracks(MusicIntent(energy_curve=[0.5] * 16), [])
    assert ranked == []
    assert library_gap(ranked) is True


def test_breakdown_includes_shape_gate_key():
    curve = [float(i) / 15 for i in range(16)]
    intent = MusicIntent(energy_curve=curve)
    ranked = rank_tracks(intent, [_tf("/m/a.mp3", energy_curve=curve)])
    bd = ranked[0][2]
    assert "shape_gate" in bd
    # perfect shape -> gate must be 1.0
    assert bd["shape_gate"] == 1.0


def test_soft_gate_high_shape_beats_low_shape_despite_perfect_other_axes():
    """Core regression: a track with high shape (0.92) but imperfect valence must rank above
    a track with low shape (0.75) whose other axes (tempo/mode/valence/duration) are all 1.0.
    This is the exact failure the 2026-06-26 tuning negotiation surfaced: a 0.795-shape track was
    winning on easy axes when shape is perceptually dominant for video."""
    curve = [float(i) / 15 for i in range(16)]

    # Track A: good shape (0.92 ≥ SHAPE_FLOOR=0.85 → gate=1.0), but valence off by 0.4
    close_curve = [min(1.0, x + 0.08) for x in curve]  # shape ≈ 0.92 (MAD ≈ 0.08)
    track_a = _tf("/m/a.mp3", energy_curve=close_curve, valence=0.2,  # intent default=0.5 → diff=0.3
                  tempo_bpm=120.0, mode="major", duration_s=120.0)

    # Track B: weak shape (0.75 < SHAPE_FLOOR → gate penalty), all other axes = 1.0
    bad_curve = [min(1.0, x + 0.25) for x in curve]   # shape ≈ 0.75 (MAD ≈ 0.25)
    track_b = _tf("/m/b.mp3", energy_curve=bad_curve, valence=0.5,
                  tempo_bpm=120.0, mode="major", duration_s=120.0)

    intent = MusicIntent(energy_curve=curve, valence=0.5, tempo_lo=0.0, tempo_hi=0.0,
                         mode_pref="any", target_duration_s=120.0)
    ranked = rank_tracks(intent, [track_a, track_b])

    totals = {tf.path: s for s, tf, _ in ranked}
    bds = {tf.path: bd for _, tf, bd in ranked}

    # Sanity: track B should have lower shape than track A
    assert bds["/m/b.mp3"]["shape"] < bds["/m/a.mp3"]["shape"]
    # Sanity: B's gate < 1.0 (shape below floor)
    assert bds["/m/b.mp3"]["shape_gate"] < 1.0
    # Core assertion: gated total of B must be below A's
    assert totals["/m/b.mp3"] < totals["/m/a.mp3"], (
        f"B gated total {totals['/m/b.mp3']} should be < A total {totals['/m/a.mp3']}"
    )
    # Top pick is A
    assert ranked[0][1].path == "/m/a.mp3"


def test_load_features_lib_reads_sidecars_skips_missing(tmp_path):
    from composerv.music.features import write_sidecar
    from composerv.music.library import load_features_lib

    a = tmp_path / "a.mp3"
    a.write_bytes(b"\x00")
    b = tmp_path / "b.mp3"
    b.write_bytes(b"\x00")
    write_sidecar(_tf(str(a)))  # only a has a sidecar
    lib = {"calm": [str(a), str(b)]}
    feats = load_features_lib(lib)
    assert [f.path for f in feats] == [str(a)]
