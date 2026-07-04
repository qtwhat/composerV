import json

from composerv.index.probe import MediaInfo
from composerv.models import TrackFeatures
from composerv.music.features import write_sidecar
from composerv.store.db import Store


def _seed_store(tmp_path):
    db = Store(str(tmp_path / "c.db"))
    info = MediaInfo(path=str(tmp_path / "a.mp4"), duration_s=10.0, kind="video",
                     capture_time="2025-12-14 09:00:00")
    db.upsert_asset(info)
    db.set_clip_summary(info.path, "a walk up the hill")
    return db, info


def test_selection_runs_after_director_and_records_rationale(tmp_path):
    from composerv.director.montage import build_director_montage

    music = tmp_path / "music"
    (music / "calm").mkdir(parents=True)
    rise = [i / 15 for i in range(16)]
    fall = [1 - i / 15 for i in range(16)]
    for name, curve in (("rise.mp3", rise), ("fall.mp3", fall)):
        p = music / "calm" / name
        p.write_bytes(b"\x00")
        write_sidecar(TrackFeatures(path=str(p), duration_s=120.0, tempo_bpm=110.0,
                                    beat_times=[i * 0.5 for i in range(240)],
                                    mode="major", valence=0.6, energy_curve=curve,
                                    source="test", license="CC0"))

    db, info = _seed_store(tmp_path)
    reply = json.dumps({
        "feeling": "calm", "arc": "quiet build to a hilltop peak",
        "energy_curve": rise, "tempo_lo": 90.0, "tempo_hi": 130.0,
        "mode_pref": "major", "valence": 0.6, "target_duration_s": 8.0,
        "segments": [{"clip_id": "a", "in_s": 0.0, "out_s": 4.0, "kind": "moment",
                      "duck_music": False, "reason": "the walk"}],
    })

    plans = build_director_montage(
        db, [info.path], music_dir=str(music), feeling="calm",
        director_fn=lambda _p: reply,
        beat_fn=lambda _t: (110.0, [i * 0.5 for i in range(240)]),
        visual_fn=lambda *_: [], vad_fn=lambda *_: [],
    )
    assert plans
    plan = plans[0]
    assert plan.track.endswith("rise.mp3")            # rise track matches rise intent
    assert plan.intent is not None and len(plan.intent.energy_curve) == 16
    assert plan.match_score > 0.0
    assert "shape" in plan.match_breakdown
    assert plan.library_gap is False
    assert plan.intention.music is not None
    assert plan.intention.music.path.endswith("rise.mp3")


def test_per_day_target_duration_differs(tmp_path):
    from composerv.director.montage import build_director_montage

    music = tmp_path / "music"
    (music / "calm").mkdir(parents=True)
    rise = [i / 15 for i in range(16)]
    p = music / "calm" / "rise.mp3"
    p.write_bytes(b"\x00")
    write_sidecar(TrackFeatures(path=str(p), duration_s=600.0, tempo_bpm=110.0,
                                beat_times=[i * 0.5 for i in range(1200)],
                                mode="major", valence=0.6, energy_curve=rise,
                                source="t", license="CC0"))
    # two clips on two different days, with different total lengths
    db = Store(str(tmp_path / "c.db"))
    infos = []
    for i, (cap, dur) in enumerate([("2025-12-14 09:00:00", 10.0),
                                    ("2025-12-15 09:00:00", 10.0)]):
        mi = MediaInfo(path=str(tmp_path / f"c{i}.mp4"), duration_s=dur, kind="video",
                       capture_time=cap)
        db.upsert_asset(mi)
        db.set_clip_summary(mi.path, f"day {i}")
        infos.append(mi)
    reply = json.dumps({
        "feeling": "calm", "arc": "x", "energy_curve": rise,
        "target_duration_s": 999.0,  # global value; per-day must override this
        "segments": [
            {"clip_id": "c0", "in_s": 0.0, "out_s": 3.0, "kind": "moment",
             "duck_music": False, "reason": "d1"},
            {"clip_id": "c1", "in_s": 0.0, "out_s": 6.0, "kind": "moment",
             "duck_music": False, "reason": "d2"},
        ],
    })
    plans = build_director_montage(
        db, [i.path for i in infos], music_dir=str(music), feeling="calm",
        director_fn=lambda _p: reply,
        beat_fn=lambda _t: (110.0, [i * 0.5 for i in range(1200)]),
        visual_fn=lambda *_: [], vad_fn=lambda *_: [])
    assert len(plans) == 2
    durs = sorted(pl.intent.target_duration_s for pl in plans)
    # day 1 ~3s, day 2 ~6s: distinct, derived per-day, not the global 999
    assert durs[0] < durs[1]
    assert all(d < 999.0 for d in durs)


def test_falls_back_to_suggest_track_when_no_sidecars(tmp_path):
    from composerv.director.montage import build_director_montage

    music = tmp_path / "music"
    (music / "calm").mkdir(parents=True)
    (music / "calm" / "only.mp3").write_bytes(b"\x00")  # no sidecar
    db, info = _seed_store(tmp_path)
    reply = json.dumps({"feeling": "calm", "arc": "x", "energy_curve": [0.5] * 16,
                        "segments": [{"clip_id": "a", "in_s": 0.0, "out_s": 3.0,
                                      "kind": "moment", "duck_music": False, "reason": "r"}]})
    plans = build_director_montage(
        db, [info.path], music_dir=str(music), feeling="calm",
        director_fn=lambda _p: reply,
        beat_fn=lambda _t: (100.0, [i * 0.5 for i in range(120)]),
        visual_fn=lambda *_: [], vad_fn=lambda *_: [])
    assert plans[0].track.endswith("only.mp3")     # suggest_track fallback


def test_library_gap_true_when_no_candidate_clears_threshold(tmp_path):
    """library_gap=True when every track scores below ACCEPT_THRESHOLD=0.6.

    Score construction (DEFAULT_WEIGHTS: shape=0.5, tempo=0.2, valence=0.15, mode=0.1, duration=0.05):
      Intent: energy_curve = rising (0/15 .. 15/15), tempo_lo=100, tempo_hi=120,
              mode_pref="major", valence=0.8, target_duration_s=60.

      Track:  energy_curve = flat 0.0 (16 points), tempo_bpm=110, mode="minor", valence=0.2,
              duration_s=120.

      shape   = 1 - MAD(rising, flat-0)
                MAD = mean(|i/15 - 0| for i in 0..15) = (0+1+...+15)/(15*16) = 120/240 = 0.5
                => shape_score = 0.5
      tempo   = 1.0  (110 is inside [100, 120])
      mode    = 0.0  (minor != major)
      valence = 1 - |0.2 - 0.8| = 0.4
      duration= 1.0  (120 >= 60)

      total = 0.5*0.5 + 0.2*1.0 + 0.15*0.4 + 0.1*0.0 + 0.05*1.0
            = 0.25  + 0.20  + 0.06  + 0.0   + 0.05
            = 0.56  < 0.6  => library_gap fires, least-bad track still chosen.
    """
    from composerv.director.montage import build_director_montage

    music = tmp_path / "music"
    (music / "calm").mkdir(parents=True)

    rising = [i / 15 for i in range(16)]
    flat_low = [0.0] * 16

    # The one library track: designed to score 0.56 against the intent below.
    p = music / "calm" / "mismatch.mp3"
    p.write_bytes(b"\x00")
    write_sidecar(TrackFeatures(
        path=str(p), duration_s=120.0, tempo_bpm=110.0,
        beat_times=[i * 0.5 for i in range(240)],
        mode="minor",       # mismatches intent's "major" -> mode_score=0.0
        valence=0.2,        # far from intent's 0.8 -> valence_score=0.4
        energy_curve=flat_low,  # flat vs rising -> shape_score=0.5
        source="test", license="CC0",
    ))

    db, info = _seed_store(tmp_path)

    # Intent: rising energy, major, high valence — the opposite of the track above.
    reply = json.dumps({
        "feeling": "calm", "arc": "quiet build to peak",
        "energy_curve": rising,
        "tempo_lo": 100.0, "tempo_hi": 120.0,
        "mode_pref": "major", "valence": 0.8,
        "target_duration_s": 60.0,
        "segments": [{"clip_id": "a", "in_s": 0.0, "out_s": 4.0, "kind": "moment",
                      "duck_music": False, "reason": "the walk"}],
    })

    plans = build_director_montage(
        db, [info.path], music_dir=str(music), feeling="calm",
        director_fn=lambda _p: reply,
        beat_fn=lambda _t: (110.0, [i * 0.5 for i in range(240)]),
        visual_fn=lambda *_: [], vad_fn=lambda *_: [],
    )

    assert plans, "expected at least one plan"
    plan = plans[0]

    # library_gap must be True: best track scored below 0.6
    assert plan.library_gap is True, (
        f"expected library_gap=True but got {plan.library_gap!r}; "
        f"match_score={plan.match_score}"
    )
    # least-bad track still chosen (not None / empty)
    assert plan.track, "expected a track path even when library_gap is True"
    assert plan.track.endswith("mismatch.mp3"), f"unexpected track: {plan.track}"
    # match_score is below the threshold
    assert plan.match_score < 0.6, (
        f"expected match_score < 0.6 but got {plan.match_score}"
    )
