"""Director layer: prompt build, reply parse, footage-table render, mapping to IntentionList."""

from composerv.director.montage import build_director_montage
from composerv.director.plan import edit_to_intention, resolve_segments, split_edit_by_day
from composerv.director.prompt import build_director_prompt, parse_edit
from composerv.director.table import build_footage_table
from composerv.index.probe import MediaInfo
from composerv.store.db import Store


# --- prompt ---

def test_prompt_carries_table_feeling_budget_and_the_key_rules():
    p = build_director_prompt("[clip a] ...", feeling="calm", budget_s=300, sensitive=["小明"])
    assert "[clip a] ..." in p and "calm" in p and "300" in p and "小明" in p
    for rule in ["Human-led", "WHOLE", "Music is the bed", "chronological", "two steps"]:
        assert rule in p
    assert "photo" in p.lower() and "motion" in p.lower()  # how to use stills is explained
    assert "chosen AFTER" in p  # music is selected after the edit, not shown to the director


def test_prompt_uses_aesthetics_for_in_point_not_culling():
    # quality tags inform the in-point (rule 8); curation (rule 2) is by emotion/memory value, not image quality
    p = build_director_prompt("[clip a] ...", feeling="calm", budget_s=300)
    flat = " ".join(p.split())                  # collapse line-wrapping so phrases match across breaks
    assert "best ~" in p                        # the best-moment in-point hint is documented
    # A: per-moment quality tags are gone from the table, so the prompt no longer explains them
    assert "[弱/过渡]" not in p and "[清晰·构图好]" not in p
    assert "Human-led" in p                     # human-led leads curation
    assert "NOT by image quality" in flat       # curation is explicitly decoupled from aesthetics
    assert "never whether to keep a clip" in flat  # quality is in-point only, not curation


def test_parse_edit_pulls_json_after_reasoning():
    reply = (
        "Let me think. This day is mostly the rafting trip, the gold moment is the kid.\n\n"
        '{"feeling":"calm","arc":"a quiet river day",'
        '"segments":[{"clip_id":"/m/a.mp4","in_s":2.0,"out_s":7.5,"kind":"conversation",'
        '"duck_music":true,"reason":"the vlog question"},'
        '{"clip_id":"/m/b.mp4","in_s":0.0,"out_s":3.0,"kind":"moment","duck_music":false,'
        '"reason":"the view"}]}'
    )
    e = parse_edit(reply)
    assert e["feeling"] == "calm" and e["arc"] == "a quiet river day"
    assert [s["clip_id"] for s in e["segments"]] == ["/m/a.mp4", "/m/b.mp4"]
    assert e["segments"][0]["duck_music"] is True and e["segments"][0]["kind"] == "conversation"
    assert e["segments"][1]["duck_music"] is False


def test_parse_edit_drops_bad_segments_and_garbage():
    assert parse_edit("no json here")["segments"] == []
    e = parse_edit('{"segments":[{"clip_id":"x","in_s":5,"out_s":3},{"in_s":0,"out_s":1}]}')
    assert e["segments"] == []        # out<=in dropped; missing clip_id dropped


# --- footage table ---

def test_table_interleaves_visual_and_speech_by_time_with_who_and_note():
    table = build_footage_table([{
        "clip_id": "clip_a", "people": ["男孩-短发"], "note": "烤芋头, 重要", "duration": 20.0,
        "visual": [(0.0, "围着炉子"), (13.2, "特写食物")],
        "speech": [(11.3, 14.1, "这是芋头")],
    }])
    assert "[clip clip_a]" in table and "男孩-短发" in table and "note: 烤芋头, 重要" in table
    assert "len: 20s" in table
    # rows sorted by time: visual@0 < speech@11.3 < visual@13.2
    i0 = table.index("围着炉子"); i1 = table.index("这是芋头"); i2 = table.index("特写食物")
    assert i0 < i1 < i2
    assert "t=11.3-14.1s" in table


def test_table_shows_ocr_as_onscreen_text_next_to_its_frame():
    table = build_footage_table([{
        "clip_id": "c", "people": [], "duration": 8.0,
        "visual": [(0.0, "走过牌坊", "武夷山风景区"), (5.0, "比耶")],  # 2nd frame has no OCR
        "speech": [],
    }])
    assert "武夷山风景区" in table and "走过牌坊" in table and "比耶" in table
    assert "on screen" in table.lower()                       # OCR is labelled, not raw
    assert table.index("武夷山风景区") > table.index("走过牌坊")  # right after its visual row
    assert table.index("武夷山风景区") < table.index("比耶")      # and before the next frame


def test_table_marks_photo_as_still():
    table = build_footage_table([{
        "clip_id": "p1", "people": [], "photo": True,
        "visual": [(0.0, "全家福合影", "武夷山")], "speech": [],
    }])
    assert "[photo p1]" in table and "still" in table.lower()
    assert "全家福合影" in table and "武夷山" in table


def test_table_tolerates_objects_in_visual_tuple():
    # the rich getter may hand (t, text, ocr, objects); the table renders text+ocr, ignores boxes
    from composerv.analyze.clip_video import GroundedObject

    table = build_footage_table([{
        "clip_id": "c", "people": [], "visual": [
            (0.0, "人物入画", "", [GroundedObject(label="person", box=[0.1, 0.2, 0.3, 0.9])]),
        ], "speech": [],
    }])
    assert "人物入画" in table and "person" not in table  # boxes/labels are not prompt noise


# --- mapping ---

def test_edit_maps_to_segments_and_ducks_only_flagged_ones():
    edit = {"segments": [
        {"clip_id": "/m/a.mp4", "in_s": 2.0, "out_s": 7.0, "kind": "conversation",
         "duck_music": True, "reason": "talk"},
        {"clip_id": "/m/b.mp4", "in_s": 0.0, "out_s": 3.0, "kind": "moment",
         "duck_music": False, "reason": "view"},
    ]}
    il = edit_to_intention(edit, fps=30, track="satie.mp3")
    assert [s.source_id for s in il.segments] == ["/m/a.mp4", "/m/b.mp4"]
    assert il.segments[0].in_sec == 2.0 and il.segments[0].out_sec == 7.0
    assert il.music is not None and il.music.path == "satie.mp3"
    hs = il.music.highlights
    assert len(hs) == 1                       # only the conversation ducks
    assert hs[0].start_s == 0.0 and abs(hs[0].end_s - 5.0) < 1e-6  # first segment span [0,5]


def test_resolve_segments_matches_abbreviated_clip_ids():
    # the LLM often shortens a long path to its recognisable stem; resolve it back exactly.
    known = ["/media/DJI_001/DJI_20260101151915_0022_D.MP4",
             "/media/DJI_001/DJI_20260101155537_0024_D.MP4"]
    segs = [
        {"clip_id": "DJI_20260101151915_0022_D", "in_s": 0.0, "out_s": 5.0},   # abbreviated
        {"clip_id": known[1], "in_s": 1.0, "out_s": 2.0},                       # already exact
        {"clip_id": "DJI_99999999999999_9999_X", "in_s": 0.0, "out_s": 1.0},    # no match -> drop
    ]
    out = resolve_segments(segs, known)
    assert [s["clip_id"] for s in out] == [known[0], known[1]]


def test_parse_edit_captures_photo_kind_and_motion():
    text = ('{"segments":[{"clip_id":"p1","in_s":0,"out_s":3,"kind":"photo",'
            '"motion":"in","reason":"a still"}]}')
    segs = parse_edit(text)["segments"]
    assert segs[0]["kind"] == "photo" and segs[0]["motion"] == "in"


def test_edit_to_intention_photo_segment_carries_motion_and_holds():
    edit = {"segments": [
        {"clip_id": "/p/x.jpg", "in_s": 0.0, "out_s": 3.0, "kind": "photo", "motion": "out",
         "duck_music": True},  # a photo has no audio: must NOT create a duck highlight
    ]}
    il = edit_to_intention(edit, fps=30, track="t.mp3")
    s = il.segments[0]
    assert s.kind == "photo" and s.motion == "out" and s.duration_s == 3.0
    assert il.music is not None and il.music.highlights == []  # no ducking for a still


def test_edit_without_track_has_no_music():
    il = edit_to_intention({"segments": [
        {"clip_id": "/m/a.mp4", "in_s": 0.0, "out_s": 2.0, "kind": "moment", "duck_music": False}]})
    assert il.music is None and len(il.segments) == 1


def test_split_edit_by_day_groups_and_suffixes():
    day = {"a": "D1", "b": "D1", "c": "D2"}.get
    segs = [{"clip_id": k, "in_s": 0.0, "out_s": 10.0} for k in ["a", "b", "c"]]
    parts = split_edit_by_day(segs, day, max_part_s=300)
    assert [lbl for lbl, _ in parts] == ["D1", "D2"]
    big = [{"clip_id": "a", "in_s": 0.0, "out_s": 200.0} for _ in range(3)]
    p2 = split_edit_by_day(big, lambda s: "D1", max_part_s=300)
    assert [lbl for lbl, _ in p2] == ["D1（1）", "D1（2）", "D1（3）"]


def test_split_edit_merges_tiny_trailing_part_so_a_5min_reel_stays_whole():
    # ~296s on ONE day must not orphan a single 8s shot into a "part 2"; it merges back
    segs = [{"clip_id": str(i), "in_s": 0.0, "out_s": 8.0} for i in range(37)]  # 37×8 = 296s
    parts = split_edit_by_day(segs, lambda s: "D1", max_part_s=290, min_part_s=30)
    assert len(parts) == 1 and parts[0][0] == "D1"   # one whole reel, plain label (no （1）)
    assert len(parts[0][1]) == 37                     # the tail shot is kept, merged in


def test_split_edit_still_splits_when_each_part_is_substantial():
    # a genuinely long day still splits into multiple parts (no spurious merge): 5×120s -> 240/240/120
    segs = [{"clip_id": str(i), "in_s": 0.0, "out_s": 120.0} for i in range(5)]  # 600s
    parts = split_edit_by_day(segs, lambda s: "D1", max_part_s=290, min_part_s=30)
    assert [lbl for lbl, _ in parts] == ["D1（1）", "D1（2）", "D1（3）"]
    assert [len(s) for _, s in parts] == [2, 2, 1]  # the trailing 120s part is substantial, kept


def test_build_director_montage_end_to_end_with_injected_models(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    for name, ct in [("a", "2026-01-01T15:00:00"), ("b", "2026-01-01T16:00:00")]:
        s.upsert_asset(MediaInfo(path=f"/m/{name}.mp4", kind="video", duration_s=20.0,
                                 capture_time=ct), proxy_path=f"/p/{name}.mp4")
        s.set_clip_summary(f"/m/{name}.mp4", name)
    d = tmp_path / "music" / "calm"; d.mkdir(parents=True); (d / "t.mp3").write_bytes(b"x")

    # the director echoes the SHORT stem ids it was given (a, b), and over-shoots out_s on b
    reply = ('{"feeling":"calm","arc":"a day","segments":['
             '{"clip_id":"a","in_s":0.0,"out_s":4.0,"kind":"moment","duck_music":false},'
             '{"clip_id":"b","in_s":1.0,"out_s":99.0,"kind":"conversation","duck_music":true}]}')
    plans = build_director_montage(
        s, ["/m/a.mp4", "/m/b.mp4"], music_dir=str(tmp_path / "music"), feeling="calm",
        visual_fn=lambda p, dur: [(0.0, "a moment")],
        vad_fn=lambda p: [(1.0, 2.0, "hi there")],
        director_fn=lambda prompt: reply,
        beat_fn=lambda t: (120.0, [i * 0.5 for i in range(40)]),
    )
    assert len(plans) == 1                                  # both clips same day -> one part
    il = plans[0].intention
    assert [seg.source_id for seg in il.segments] == ["/m/a.mp4", "/m/b.mp4"]  # stems -> real paths
    assert il.segments[1].out_sec == 20.0                  # out_s clamped to the clip's duration
    assert il.music is not None and len(il.music.highlights) == 1  # only the conversation ducks


def test_build_director_montage_reads_cached_index_from_store(tmp_path):
    # with no visual_fn/vad_fn, the director reads the cached perception from the store and that
    # content reaches the prompt (proves the perception→table→director path uses the cache).
    s = Store(str(tmp_path / "c.db"))
    for n, ct in [("a", "2026-01-01T15:00:00"), ("b", "2026-01-01T16:00:00")]:
        s.upsert_asset(MediaInfo(path=f"/m/{n}.mp4", kind="video", duration_s=20.0,
                                 capture_time=ct), proxy_path=f"/p/{n}.mp4")
        s.set_clip_summary(f"/m/{n}.mp4", n)
    s.set_clip_moments("/m/a.mp4", [(0.0, "a kid by the grill")])
    s.set_transcript("/m/a.mp4", [(1.0, 2.0, "happy new year")])
    d = tmp_path / "music" / "calm"; d.mkdir(parents=True); (d / "t.mp3").write_bytes(b"x")

    captured = {}

    def director_fn(prompt):
        captured["p"] = prompt
        return ('{"segments":[{"clip_id":"a","in_s":0.0,"out_s":4.0,"kind":"moment",'
                '"duck_music":false}]}')

    plans = build_director_montage(
        s, ["/m/a.mp4", "/m/b.mp4"], music_dir=str(tmp_path / "music"), feeling="calm",
        director_fn=director_fn, beat_fn=lambda t: (120.0, [i * 0.5 for i in range(40)]))
    assert "a kid by the grill" in captured["p"]   # stored visual moment reached the table
    assert "happy new year" in captured["p"]        # stored transcript reached the table
    assert plans and plans[0].intention.segments[0].source_id == "/m/a.mp4"


def test_build_director_montage_surfaces_stored_ocr(tmp_path):
    # OCR cached on a moment (place-name signage) must reach the director prompt via the rich getter
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=20.0,
                             capture_time="2026-01-01T15:00:00"), proxy_path="/p/a.mp4")
    s.set_clip_summary("/m/a.mp4", "a")
    s.set_clip_moments("/m/a.mp4", [(0.0, "走过牌坊", "武夷山风景区")])
    d = tmp_path / "music" / "calm"; d.mkdir(parents=True); (d / "t.mp3").write_bytes(b"x")

    captured = {}

    def director_fn(prompt):
        captured["p"] = prompt
        return '{"segments":[{"clip_id":"a","in_s":0.0,"out_s":4.0,"kind":"moment","duck_music":false}]}'

    build_director_montage(s, ["/m/a.mp4"], music_dir=str(tmp_path / "music"), feeling="calm",
                           director_fn=director_fn, beat_fn=lambda t: (120.0, []))
    assert "武夷山风景区" in captured["p"] and "on screen" in captured["p"].lower()


def test_build_director_montage_handles_photo_assets(tmp_path):
    # a photo asset (duration 0) must not be clamped away; it becomes a held still with motion
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/p.jpg", kind="photo", duration_s=0.0, width=4000, height=3000,
                             capture_time="2026-01-01T15:00:00"), proxy_path="/p/p.jpg")
    s.set_clip_summary("/m/p.jpg", "a family photo")
    s.set_clip_moments("/m/p.jpg", [(0.0, "全家福合影", "武夷山")])
    d = tmp_path / "music" / "calm"; d.mkdir(parents=True); (d / "t.mp3").write_bytes(b"x")

    captured = {}

    def director_fn(prompt):
        captured["p"] = prompt
        return ('{"segments":[{"clip_id":"p","in_s":0,"out_s":4,"kind":"photo","motion":"in",'
                '"duck_music":false}]}')

    plans = build_director_montage(s, ["/m/p.jpg"], music_dir=str(tmp_path / "music"),
                                   feeling="calm", director_fn=director_fn,
                                   beat_fn=lambda t: (120.0, []))
    seg = plans[0].intention.segments[0]
    assert seg.kind == "photo" and seg.source_id == "/m/p.jpg" and seg.motion == "in"
    assert 1.0 <= seg.duration_s <= 8.0           # hold clamped to a sane range, not clamped to 0
    assert "[photo" in captured["p"]              # the director saw it marked as a still


def test_build_director_montage_raises_on_empty_director_reply(tmp_path):
    import pytest as _pytest
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=10.0,
                             capture_time="2026-01-01T15:00:00"), proxy_path="/p/a.mp4")
    s.set_clip_summary("/m/a.mp4", "a")
    d = tmp_path / "music" / "calm"; d.mkdir(parents=True); (d / "t.mp3").write_bytes(b"x")
    with _pytest.raises(RuntimeError, match="no usable edit"):
        build_director_montage(
            s, ["/m/a.mp4"], music_dir=str(tmp_path / "music"), feeling="calm",
            visual_fn=lambda p, dur: [], vad_fn=lambda p: [],
            director_fn=lambda prompt: "",                  # empty reply (e.g. a swallowed timeout)
            beat_fn=lambda t: (120.0, [i * 0.5 for i in range(40)]))


def test_table_renders_quality_tag_and_best_header():
    table = build_footage_table([{
        "clip_id": "c", "people": [], "duration": 10.0, "best_t": 3.2,
        "visual": [(0.0, "走进来", "", [], "[弱/过渡]"), (3.2, "特写", "", [], "[清晰·构图好]")],
        "speech": [],
    }])
    assert "best ~3.2s" in table                    # per-clip best moment in the header
    assert "走进来  [弱/过渡]" in table              # tag appended to its visual row
    assert "特写  [清晰·构图好]" in table


def test_table_without_aesthetics_is_unchanged():
    table = build_footage_table([{
        "clip_id": "c", "people": [], "duration": 10.0,
        "visual": [(0.0, "走进来")], "speech": [],
    }])
    assert "走进来" in table and "best ~" not in table   # no aesthetics -> no header, no tag


def test_build_director_montage_surfaces_aesthetics(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=20.0,
                             capture_time="2026-01-01T15:00:00"), proxy_path="/p/a.mp4")
    s.set_clip_summary("/m/a.mp4", "a")
    s.set_clip_moments("/m/a.mp4", [(0.0, "走过牌坊"), (3.0, "孩子特写")])
    s.set_clip_aesthetics("/m/a.mp4", 3.0, [(0.0, -0.5, False), (3.0, 0.9, False)])
    d = tmp_path / "music" / "calm"; d.mkdir(parents=True); (d / "t.mp3").write_bytes(b"x")

    captured = {}

    def director_fn(prompt):
        captured["p"] = prompt
        return '{"segments":[{"clip_id":"a","in_s":3.0,"out_s":6.0,"kind":"moment","duck_music":false}]}'

    build_director_montage(s, ["/m/a.mp4"], music_dir=str(tmp_path / "music"), feeling="calm",
                           director_fn=director_fn, beat_fn=lambda t: (120.0, []))
    assert "best ~3.0s" in captured["p"]              # per-clip best moment (in-point anchor) still reaches the director
    assert "孩子特写" in captured["p"] and "走过牌坊" in captured["p"]   # the moments themselves are there
    # A: per-moment quality tags are NOT in the table — image quality must not drive curation
    assert "[清晰·构图好]" not in captured["p"] and "[弱/过渡]" not in captured["p"]


def test_default_director_uses_opus_46(monkeypatch):
    # the editorial judgment runs on Opus 4.6 (user preference), not the claude_text default
    from composerv.director import montage as M

    seen = {}

    def fake_claude_text(prompt, model="claude-sonnet-4-6", timeout=300, proxy=None):
        seen["model"] = model
        return "{}"

    monkeypatch.setattr("composerv.analyze.backends.claude_cli.claude_text", fake_claude_text)
    M._default_director("hi")
    assert seen["model"] == "claude-opus-4-6"


def test_prompt_includes_human_brief_when_given():
    p = build_director_prompt("[clip a] ...", feeling="calm", budget_s=120,
                              brief_context="武夷山家庭游", brief_style="轻快，多留孩子")
    assert "HUMAN BRIEF" in p and "武夷山家庭游" in p and "轻快，多留孩子" in p
    assert p.index("HUMAN BRIEF") < p.index("HOW TO EDIT")   # before the rules
    assert p.index("HUMAN BRIEF") < p.index("[clip a]")       # before the footage table


def test_prompt_omits_brief_section_when_empty():
    p = build_director_prompt("[clip a] ...", feeling="calm", budget_s=120)
    assert "HUMAN BRIEF" not in p


def test_montage_injects_brief_and_person_labels_into_prompt(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=20.0,
                             capture_time="2026-01-01T15:00:00"), proxy_path="/p/a.mp4")
    s.set_clip_summary("/m/a.mp4", "a")
    s.replace_faces("/m/a.mp4", [(1.0, [0, 0, 10, 100], [1, 0], "")])
    s.upsert_person(0)
    s.set_face_person(s.get_faces("/m/a.mp4")[0].face_id, 0)
    s.set_person_name(0, "小明")
    s.set_person_note(0, "我女儿")
    s.set_brief("trip", context="武夷山家庭游", style="轻快，多留孩子")
    (tmp_path / "music").mkdir()

    captured = {}

    def director_fn(prompt):
        captured["p"] = prompt
        return '{"segments":[{"clip_id":"a","in_s":0.0,"out_s":4.0,"kind":"moment","duck_music":false}]}'

    build_director_montage(
        s, ["/m/a.mp4"], music_dir=str(tmp_path / "music"), feeling="calm",
        visual_fn=lambda p, dur: [(0.0, "a moment")], vad_fn=lambda p: [],
        director_fn=director_fn, beat_fn=lambda t: (120.0, [i * 0.5 for i in range(40)]),
        brief=s.get_brief("trip"))
    assert "HUMAN BRIEF" in captured["p"] and "武夷山家庭游" in captured["p"]
    assert "小明（我女儿）" in captured["p"]   # person note rides into the who field
