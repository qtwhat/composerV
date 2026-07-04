"""Tests for the clarity summary (what-this-clip-is). Pure parts + injected-run wiring."""

import shutil

import pytest

from composerv.clarity.summarize import (
    ClaritySummary,
    build_clarity_prompt,
    parse_clarity,
    summarize_clip,
)


def _ffmpeg_or_skip():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")


def test_build_clarity_prompt_asks_what_this_is_not_per_frame():
    p = build_clarity_prompt([(0.0, "/f/0.jpg"), (2.0, "/f/1.jpg")])
    low = p.lower()
    assert "/f/0.jpg" in p and "t=0.0" in p     # lists paths (for the Claude Read path) + timestamps
    assert "description" in low and "json" in low
    assert "who" in low and "where" in low       # identify who/doing what/where
    assert "1-3" in p                            # short, not a frame-by-frame dump


def test_parse_clarity_extracts_description_from_json():
    assert parse_clarity('noise {"description":"a woman does skincare"} tail') == "a woman does skincare"


def test_parse_clarity_falls_back_to_plain_text():
    assert parse_clarity("  a woman does skincare  ") == "a woman does skincare"


def test_parse_clarity_empty_for_garbage_json():
    assert parse_clarity("{}") == ""                  # model gave no description
    assert parse_clarity('{"foo":"bar"}') == ""       # wrong key, not usable


def test_parse_clarity_recovers_truncated_description():
    # model hit the token cap before closing the JSON
    assert parse_clarity('{\n  "description": "a woman does skincare while a man watches') \
        == "a woman does skincare while a man watches"


def test_parse_clarity_prefers_real_value_over_placeholder_template():
    # if both an unfilled template and a real object appear, take the real one
    text = '{"description":"<1-3 sentence what-this-is>"}\n{"description":"a drone flies over a river"}'
    assert parse_clarity(text) in ("a drone flies over a river",)


def test_summarize_clip_via_injected_run_defaults_local(tmp_path):
    _ffmpeg_or_skip()
    from composerv.devtools import make_cfr_test_clip

    clip = str(tmp_path / "c.mp4")
    make_cfr_test_clip(clip, seconds=4.0, label="A")

    seen = {}

    def fake_run(prompt, image_paths):
        seen["n"] = len(image_paths)
        seen["prompt"] = prompt
        return '{"description":"a test pattern animates"}'

    cs = summarize_clip(clip, duration_s=4.0, run=fake_run, target_frames=4)
    assert isinstance(cs, ClaritySummary)
    assert cs.text == "a test pattern animates"
    assert cs.source == "local"          # default engine
    assert seen["n"] >= 1                 # frames sampled and passed to the runner
    assert "t=" in seen["prompt"]


def test_summarize_clip_source_label_for_refine(tmp_path):
    _ffmpeg_or_skip()
    from composerv.devtools import make_cfr_test_clip

    clip = str(tmp_path / "c.mp4")
    make_cfr_test_clip(clip, seconds=4.0, label="A")
    cs = summarize_clip(clip, duration_s=4.0, run=lambda p, imgs: "just text",
                        source="claude", target_frames=4)
    assert cs.text == "just text"
    assert cs.source == "claude"
