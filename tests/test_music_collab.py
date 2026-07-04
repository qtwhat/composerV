import json
import os

from composerv.models import MusicIntent, TrackFeatures


def _tf(path, curve, **kw):
    base = dict(duration_s=120.0, tempo_bpm=110.0, mode="major", valence=0.6)
    base.update(kw)
    return TrackFeatures(path=path, energy_curve=curve, **base)


def test_director_role_parses_intent_including_arc_text():
    from composerv.music.collab import director_role

    reply = json.dumps({"energy_curve": [0.5] * 16, "tempo_lo": 90, "tempo_hi": 130,
                        "mode_pref": "major", "valence": 0.6, "target_duration_s": 60,
                        "arc_text": "quiet to peak"})
    mi = director_role("TABLE", run=lambda _p: reply)
    assert isinstance(mi, MusicIntent) and len(mi.energy_curve) == 16
    assert mi.arc_text == "quiet to peak"   # arc_text key is honored (not silently dropped)


def test_scorer_role_uses_shipped_ranking_and_attaches_prose():
    from composerv.music.collab import scorer_role

    rise = [i / 15 for i in range(16)]
    fall = [1 - i / 15 for i in range(16)]
    lib = [_tf("/m/rise.mp3", rise), _tf("/m/fall.mp3", fall)]
    intent = MusicIntent(energy_curve=rise)
    prose = json.dumps([{"path": "/m/rise.mp3", "why_match": "tracks the build",
                         "why_not": "ends slightly bright"}])
    cands = scorer_role(intent, lib, run=lambda _p: prose, k=2)
    assert cands[0].path == "/m/rise.mp3"          # deterministic ranking, not the LLM
    assert cands[0].match_score == max(c.match_score for c in cands)
    assert cands[0].why_match == "tracks the build"
    assert cands[0].audio_reality["mode"] == "major"


def test_evaluator_role_parses_verdict():
    from composerv.music.collab import evaluator_role

    reply = json.dumps({"pass": False, "reasons": ["climax too early"],
                        "axes": {"shape_aligned": False}, "ask": "push the peak later"})
    v = evaluator_role({"x": 1}, run=lambda _p: reply)
    assert v.passed is False and v.ask == "push the peak later"


def test_round_record_reserves_human_checkpoint_slot(tmp_path):
    from composerv.music.collab import negotiate

    rise = [i / 15 for i in range(16)]
    lib = [_tf("/m/rise.mp3", rise)]
    director = lambda _p: json.dumps({"energy_curve": rise, "arc_text": "x"})
    scorer = lambda _p: json.dumps([{"path": "/m/rise.mp3", "why_match": "y", "why_not": ""}])
    passing = lambda _p: json.dumps({"pass": True, "reasons": [], "axes": {}, "ask": ""})
    records = negotiate("reel0", "TABLE", lib, max_rounds=4,
                        runs={"director": director, "scorer": scorer, "evaluator": passing},
                        out_dir=str(tmp_path))
    # converged on round 1; the human checkpoint exists but is unreviewed (auto != final)
    assert records[-1].converged is True
    assert records[-1].human_checkpoint.reviewed is False
    assert records[-1].human_checkpoint.approved is None


def test_negotiate_stops_at_round_cap(tmp_path):
    from composerv.music.collab import negotiate

    rise = [i / 15 for i in range(16)]
    lib = [_tf("/m/rise.mp3", rise)]
    director = lambda _p: json.dumps({"energy_curve": rise, "arc_text": "x"})
    scorer = lambda _p: json.dumps([{"path": "/m/rise.mp3", "why_match": "y", "why_not": ""}])
    never_pass = lambda _p: json.dumps({"pass": False, "reasons": ["no"], "axes": {}, "ask": "again"})
    records = negotiate("reel1", "TABLE", lib, max_rounds=4,
                        runs={"director": director, "scorer": scorer, "evaluator": never_pass},
                        out_dir=str(tmp_path))
    assert len(records) == 4
    assert all(not r.converged for r in records)
    files = os.listdir(os.path.join(str(tmp_path), "reel1"))
    assert any(f.startswith("round-") for f in files)


def test_negotiate_library_gap_when_nothing_clears(tmp_path):
    from composerv.music.collab import negotiate

    fall = [1 - i / 15 for i in range(16)]
    lib = [_tf("/m/fall.mp3", fall, mode="minor", valence=0.0, tempo_bpm=70.0)]
    director = lambda _p: json.dumps({"energy_curve": [i / 15 for i in range(16)],
                                      "mode_pref": "major", "tempo_lo": 200, "tempo_hi": 210,
                                      "valence": 1.0, "arc_text": "rise"})
    scorer = lambda _p: json.dumps([{"path": "/m/fall.mp3", "why_match": "", "why_not": "wrong shape"}])
    evalr = lambda _p: json.dumps({"pass": False, "reasons": ["gap"], "axes": {}, "ask": "x"})
    records = negotiate("reel2", "TABLE", lib, max_rounds=2,
                        runs={"director": director, "scorer": scorer, "evaluator": evalr},
                        out_dir=str(tmp_path))
    assert records[-1].verdict.library_gap is True   # set by the driver, not the LLM


def test_evaluator_pass_does_not_converge_without_acceptance(tmp_path):
    """Guard the convergence AND: evaluator.passed=True alone must NOT converge when no track
    clears ACCEPT_THRESHOLD (chosen_path stays None).  A regression that weakened the AND to
    an OR would let this test through falsely."""
    from composerv.music.collab import negotiate

    # Single track whose features are maximally mismatched against the director intent:
    # opposite energy shape, tempo far outside the requested band, wrong mode, wrong valence.
    fall = [1 - i / 15 for i in range(16)]
    lib = [_tf("/m/fall.mp3", fall, mode="minor", valence=0.0, tempo_bpm=70.0)]

    # Director always asks for a fast-tempo rising track — the library track will never clear
    # ACCEPT_THRESHOLD because tempo_bpm=70 vs tempo_lo=200/tempo_hi=210 is a hard miss.
    director = lambda _p: json.dumps({"energy_curve": [i / 15 for i in range(16)],
                                      "mode_pref": "major", "tempo_lo": 200, "tempo_hi": 210,
                                      "valence": 1.0, "arc_text": "rise"})
    scorer = lambda _p: json.dumps([{"path": "/m/fall.mp3", "why_match": "", "why_not": "wrong shape"}])

    # Evaluator ALWAYS returns passed=True — this is the key difference from the library_gap test.
    # If convergence were "evaluator pass OR chosen_path", the loop would stop on round 1.
    always_pass = lambda _p: json.dumps({"pass": True, "reasons": [], "axes": {}, "ask": ""})

    max_rounds = 3
    records = negotiate("reel3", "TABLE", lib, max_rounds=max_rounds,
                        runs={"director": director, "scorer": scorer, "evaluator": always_pass},
                        out_dir=str(tmp_path))

    # Must run all rounds — no early exit despite evaluator always passing.
    assert len(records) == max_rounds
    # chosen_path is None every round, so converged must be False every round.
    assert all(not r.converged for r in records)
    # Driver must have flagged library_gap on the final record.
    assert records[-1].verdict.library_gap is True
