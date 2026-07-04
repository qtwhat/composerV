"""Tests for the local (mlx-vlm) true-video understanding backend's pure parts.

The actual on-device model run is validated manually (needs the weights); here we test the
prompt and the injectable-run wiring with the shared parse_understanding.
"""

import shutil

import pytest

from composerv.analyze.backends.qwen_mlx import (
    _ground_moments,
    build_local_video_prompt,
    understand_clip_local,
)
from composerv.analyze.clip_video import ClipMoment, ClipUnderstanding


def test_ground_moments_assigns_real_timestamps_by_position():
    moments = [ClipMoment(t=0.0, text="a"), ClipMoment(t=0.0, text="b"), ClipMoment(t=0.0, text="c")]
    out = _ground_moments(moments, [0.0, 2.0, 4.0])
    assert [m.t for m in out] == [0.0, 2.0, 4.0]


def test_ground_moments_truncates_extra_moments_to_frame_count():
    # model emitted more lines than there are frames -> keep only as many as frames, all grounded
    moments = [ClipMoment(t=0.0, text=c) for c in "abcd"]
    out = _ground_moments(moments, [0.0, 2.0])
    assert [(m.t, m.text) for m in out] == [(0.0, "a"), (2.0, "b")]


def test_ground_moments_mismatch_does_not_collapse_to_zero():
    # the bug we hit on 7B: a count mismatch used to leave every t at 0.0.
    moments = [ClipMoment(t=0.0, text="a"), ClipMoment(t=0.0, text="b")]
    out = _ground_moments(moments, [1.0, 5.0, 9.0])
    assert [m.t for m in out] == [1.0, 5.0]  # each grounded to its frame time, not all 0.0


def test_ground_moments_handles_empty():
    assert _ground_moments([], [1.0, 2.0]) == []
    assert _ground_moments([ClipMoment(t=3.0, text="a")], []) == []  # no frames -> nothing to ground


def test_local_prompt_lists_timestamps_and_asks_temporal_json():
    p = build_local_video_prompt([(0.0, "/f/0.jpg"), (2.0, "/f/1.jpg")])
    assert "t=0.0" in p and "t=2.0" in p
    low = p.lower()
    assert "over time" in low or "happens" in low
    assert "json" in low and "summary" in low and "moments" in low


def test_understand_clip_local_parses_via_injected_run(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    from composerv.devtools import make_cfr_test_clip

    clip = str(tmp_path / "c.mp4")
    make_cfr_test_clip(clip, seconds=4.0, label="A")
    canned = '{"summary":"a run unfolds","moments":[{"t":0,"happening":"start"},{"t":3,"happening":"end"}]}'

    seen = {}

    def fake_run(image_paths, prompt, model, max_tokens, timeout):
        seen["n_images"] = len(image_paths)
        seen["prompt"] = prompt
        return canned

    u = understand_clip_local(clip, duration_s=4.0, run=fake_run, target_frames=4)
    assert isinstance(u, ClipUnderstanding)
    assert u.summary == "a run unfolds"
    # moments are grounded to REAL frame timestamps by position (model's own t are unreliable):
    # first frame is t=0.0, times are distinct and increasing, never collapsed to 0.0
    assert len(u.moments) >= 1
    assert u.moments[0].t == 0.0
    assert [m.t for m in u.moments] == sorted(m.t for m in u.moments)
    assert len({m.t for m in u.moments}) == len(u.moments)
    assert seen["n_images"] >= 1  # frames were actually sampled and passed
    assert "t=" in seen["prompt"]  # timestamps fed to the model


def test_extract_generation_strips_prompt_echo_and_stats():
    from composerv.analyze.backends.qwen_mlx import _extract_generation

    raw = (
        "==========\nFiles: ['/f/0.jpg']\n\n"
        "Prompt: <|im_start|>system\nYou are a helpful assistant.<|im_end|>\n"
        '<|im_start|>user\n...Reply with {"description":"<placeholder>"}.<|im_end|>\n'
        "<|im_start|>assistant\n\n"
        '{"description":"a woman does skincare"}\n'
        "==========\nPrompt: 123 tokens, 45.6 tokens-per-sec\nGeneration: 20 tokens\n"
    )
    out = _extract_generation(raw)
    assert out == '{"description":"a woman does skincare"}'
    assert "im_start" not in out and "placeholder" not in out and "tokens" not in out


def test_extract_generation_passthrough_without_marker():
    from composerv.analyze.backends.qwen_mlx import _extract_generation

    # injected/fake runs return clean text with no chat-template markers: leave it alone
    assert _extract_generation('{"description":"x"}') == '{"description":"x"}'
