from composerv.director.prompt import build_director_prompt, parse_edit


def test_prompt_asks_for_16_point_energy_curve_and_omits_concrete_track():
    p = build_director_prompt("TABLE", feeling="calm", budget_s=120.0)
    assert "energy_curve" in p
    assert "16" in p
    # director stays blind to the chosen track: the schema never shows a concrete bpm tempo
    assert "bpm" not in p.lower()


def test_parse_edit_extracts_music_intent():
    reply = """reasoning...
    {"feeling":"calm","arc":"quiet to peak to calm",
     "energy_curve":[0.2,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.8,0.7,0.6,0.5,0.4,0.4,0.3,0.3],
     "tempo_lo":90.0,"tempo_hi":120.0,"mode_pref":"major","valence":0.6,
     "target_duration_s":120.0,
     "segments":[{"clip_id":"a","in_s":0.0,"out_s":3.0,"kind":"moment","duck_music":false,"reason":"x"}]}
    """
    out = parse_edit(reply)
    mi = out["music_intent"]
    assert len(mi["energy_curve"]) == 16
    assert mi["tempo_lo"] == 90.0 and mi["tempo_hi"] == 120.0
    assert mi["mode_pref"] == "major" and mi["valence"] == 0.6
    assert mi["target_duration_s"] == 120.0
    assert mi["arc_text"] == "quiet to peak to calm"   # prose arc preserved
    assert len(out["segments"]) == 1                    # segments still parsed


def test_parse_edit_missing_intent_returns_safe_default():
    reply = '{"feeling":"sad","arc":"slow","segments":[]}'
    mi = parse_edit(reply)["music_intent"]
    assert mi["energy_curve"] == []
    assert mi["tempo_lo"] == 0.0 and mi["tempo_hi"] == 0.0
    assert mi["mode_pref"] == "any"
    assert mi["arc_text"] == "slow"


def test_parse_edit_malformed_curve_clamped_to_16_in_range():
    reply = ('{"feeling":"calm","arc":"a",'
             '"energy_curve":[0.5,2.0,-1.0,"x",0.4],"segments":[]}')
    mi = parse_edit(reply)["music_intent"]
    assert len(mi["energy_curve"]) == 16
    assert all(0.0 <= v <= 1.0 for v in mi["energy_curve"])
