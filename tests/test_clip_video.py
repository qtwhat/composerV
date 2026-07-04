"""Tests for analyze.clip_video: clip-level understanding from a full-coverage frame
sequence (temporal/event reasoning), with moments grounded to real timestamps.
Pure prompt/parse tested; the live claude_read call is injectable.
"""

import json

from composerv.analyze.clip_video import (
    ClipMoment,
    ClipUnderstanding,
    GroundedObject,
    build_ground_prompt,
    build_photo_prompt,
    build_video_prompt,
    normalize_objects,
    parse_caption,
    parse_grounding,
    parse_understanding,
)


def test_prompt_lists_ordered_frames_with_timestamps():
    p = build_video_prompt([(0.0, "/f/0.jpg"), (2.0, "/f/1.jpg"), (4.0, "/f/2.jpg")])
    assert "/f/0.jpg" in p and "/f/2.jpg" in p
    assert "t=0.0" in p and "t=4.0" in p
    low = p.lower()
    assert "order" in low and "over time" in low  # asks for temporal reasoning
    assert "json" in low


def test_parse_understanding_full():
    text = json.dumps({
        "summary": "A caregiver tends to a woman, who later walks outside.",
        "moments": [
            {"t": 0.0, "happening": "caregiver leans in"},
            {"t": 18.0, "happening": "the woman stands and turns away"},
        ],
    })
    u = parse_understanding(text)
    assert isinstance(u, ClipUnderstanding)
    assert u.summary.startswith("A caregiver")
    assert [m.t for m in u.moments] == [0.0, 18.0]
    assert u.moments[1].text == "the woman stands and turns away"


def test_parse_understanding_skips_prose_preamble():
    text = 'Sure, here it is:\n{"summary":"s","moments":[{"t":3,"happening":"x"}]}'
    u = parse_understanding(text)
    assert u.summary == "s"
    assert u.moments[0].t == 3.0 and u.moments[0].text == "x"


def test_parse_understanding_garbage():
    u = parse_understanding("nope")
    assert u.summary == "" and u.moments == []


# --- grounding + OCR (per-frame single-image pass) ---


def test_moment_carries_ocr_and_objects_defaulting_empty():
    m = ClipMoment(t=1.0, text="x")
    assert m.ocr == "" and m.objects == []
    m2 = ClipMoment(t=1.0, text="x", ocr="武夷山", objects=[GroundedObject(label="person", box=[0.1, 0.2, 0.3, 0.4])])
    assert m2.ocr == "武夷山" and m2.objects[0].label == "person"


def test_build_ground_prompt_asks_boxes_and_ocr_no_box_for_text():
    p = build_ground_prompt()
    low = p.lower()
    assert "json" in low and "bbox_2d" in low
    assert "ocr" in low and "text" in low
    # OCR must be a string field, not an object/box (the hallucination we saw)
    assert "\"ocr\"" in p or "'ocr'" in p


def test_parse_grounding_bare_array_is_objects_ocr_empty():
    # the real shape the 7B model emitted in the probe: a bare array of {bbox_2d,label}
    text = '```json\n[{"bbox_2d":[184,91,290,252],"label":"person"}]\n```'
    objs, ocr = parse_grounding(text)
    assert ocr == ""
    assert len(objs) == 1 and objs[0].label == "person"
    assert objs[0].box == [184.0, 91.0, 290.0, 252.0]  # RAW pixel coords, normalized later


def test_parse_grounding_wrapped_with_ocr():
    text = json.dumps({"objects": [{"label": "sign", "bbox_2d": [10, 20, 30, 40]}], "ocr": "武夷山欢迎您"})
    objs, ocr = parse_grounding(text)
    assert ocr == "武夷山欢迎您"
    assert objs[0].label == "sign" and objs[0].box == [10.0, 20.0, 30.0, 40.0]


def test_parse_grounding_accepts_alt_box_keys():
    text = json.dumps([{"label": "a", "box": [1, 2, 3, 4]}, {"label": "b", "bbox": [5, 6, 7, 8]}])
    objs, _ = parse_grounding(text)
    assert [o.box for o in objs] == [[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]]


def test_parse_grounding_drops_malformed_boxes():
    text = json.dumps([{"label": "ok", "bbox_2d": [1, 2, 3, 4]},
                       {"label": "bad", "bbox_2d": [1, 2, 3]},        # too few
                       {"label": "nan", "bbox_2d": ["x", 2, 3, 4]}])  # non-numeric
    objs, _ = parse_grounding(text)
    assert [o.label for o in objs] == ["ok"]


def test_parse_grounding_garbage():
    assert parse_grounding("nope") == ([], "")


def test_parse_grounding_bare_array_with_per_element_ocr_keeps_boxes():
    # the model may attach read text per detection; the bare-array branch must still win so the
    # boxes survive (regression: an element's "ocr" key used to be mistaken for the wrapper).
    text = json.dumps([{"label": "sign", "bbox_2d": [10, 20, 30, 40], "ocr": "x"},
                       {"label": "person", "bbox_2d": [50, 60, 70, 80]}])
    objs, _ = parse_grounding(text)
    assert [o.label for o in objs] == ["sign", "person"]


def test_parse_grounding_joins_list_ocr_instead_of_repr():
    # a disobedient model may return ocr as a list; join it rather than leak Python repr brackets
    _, ocr = parse_grounding(json.dumps({"objects": [], "ocr": ["sign1", "sign2"]}))
    assert ocr == "sign1 sign2"


def test_normalize_objects_divides_by_frame_dims_and_clamps():
    objs = [GroundedObject(label="person", box=[184, 91, 290, 252])]
    out = normalize_objects(objs, 448, 252)
    x1, y1, x2, y2 = out[0].box
    assert round(x1, 2) == 0.41 and round(y1, 2) == 0.36
    assert round(x2, 2) == 0.65 and y2 == 1.0   # y2=252 -> exactly 1.0


def test_build_photo_prompt_and_parse_caption():
    p = build_photo_prompt()
    assert "caption" in p.lower() and "json" in p.lower()
    assert parse_caption('{"caption":"全家在山前合影"}') == "全家在山前合影"
    assert parse_caption("Sure:\n{\"caption\":\"a quiet street\"}") == "a quiet street"
    assert parse_caption("just a plain line\nsecond line") == "just a plain line"  # fallback
    assert parse_caption("") == ""


def test_normalize_objects_clamps_overshoot_and_drops_zero_area():
    objs = [
        GroundedObject(label="over", box=[-10, -5, 500, 300]),   # overshoots -> clamps to [0,1]
        GroundedObject(label="degenerate", box=[100, 100, 100, 100]),  # zero area -> dropped
    ]
    out = normalize_objects(objs, 448, 252)
    assert [o.label for o in out] == ["over"]
    assert out[0].box == [0.0, 0.0, 1.0, 1.0]
