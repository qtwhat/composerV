"""Tests for the tunable local understander wiring (frame selection + resolution + grounding).

The real model run is validated live; here we inject `run` to exercise the orchestration.
"""

import shutil

import pytest

from composerv.analyze.clip_video import ClipUnderstanding
from composerv.clarity.understand import understand_clip_tunable


def _ffmpeg_or_skip():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")


def test_understand_tunable_selects_frames_grounds_and_ignores_model_timestamps(tmp_path):
    _ffmpeg_or_skip()
    from composerv.devtools import make_cfr_test_clip

    clip = str(tmp_path / "c.mp4")
    make_cfr_test_clip(clip, seconds=8.0, label="A")
    # model returns 4 moments all with a bogus t=9; we must ground to real frame times
    canned = ('{"summary":"s","moments":['
              '{"t":9,"happening":"a"},{"t":9,"happening":"b"},'
              '{"t":9,"happening":"c"},{"t":9,"happening":"d"}]}')
    seen = {}

    def fake(prompt, imgs):
        seen["n"] = len(imgs)
        seen["prompt"] = prompt
        return canned

    u = understand_clip_tunable(clip, 8.0, run=fake, frames_mode="uniform",
                                max_frames=4, min_frames=4, frames_dir=str(tmp_path / "kf"))
    assert isinstance(u, ClipUnderstanding)
    assert seen["n"] == 4                                  # 4 uniform frames selected + passed
    assert u.summary == "s"
    assert [m.t for m in u.moments] == [0.0, 2.0, 4.0, 6.0]  # grounded to frames, not t=9
    assert "t=" in seen["prompt"]


def test_understand_tunable_downscales_frames_when_resolution_set(tmp_path):
    _ffmpeg_or_skip()
    from PIL import Image

    from composerv.devtools import make_cfr_test_clip

    clip = str(tmp_path / "c.mp4")
    make_cfr_test_clip(clip, seconds=4.0, label="A")
    sizes = []

    def fake(prompt, imgs):
        for p in imgs:
            sizes.append(max(Image.open(p).size))
        return '{"summary":"s","moments":[]}'

    understand_clip_tunable(clip, 4.0, run=fake, frames_mode="uniform", max_frames=3,
                            min_frames=3, max_long_side=256, frames_dir=str(tmp_path / "kf"))
    assert sizes and all(s <= 256 for s in sizes)  # frames fed to the model were downscaled


def test_understand_tunable_grounds_and_ocrs_capped_first_frames(tmp_path):
    _ffmpeg_or_skip()
    from composerv.devtools import make_cfr_test_clip

    clip = str(tmp_path / "c.mp4")
    make_cfr_test_clip(clip, seconds=8.0, label="A")
    narrative = ('{"summary":"s","moments":['
                 '{"t":0,"happening":"a"},{"t":0,"happening":"b"},'
                 '{"t":0,"happening":"c"},{"t":0,"happening":"d"}]}')

    def fake(prompt, imgs):
        if "bbox_2d" in prompt:                       # the per-frame grounding pass
            assert len(imgs) == 1                      # single-image (boxes need single context)
            return '{"objects":[{"label":"person","bbox_2d":[10,10,50,90]}],"ocr":"标语"}'
        return narrative                               # the multi-frame narrative pass

    u = understand_clip_tunable(clip, 8.0, run=fake, frames_mode="uniform", max_frames=4,
                                min_frames=4, ground=True, ocr=True, max_ground_frames=2,
                                frames_dir=str(tmp_path / "kf"))
    assert u.moments[0].objects and u.moments[0].objects[0].label == "person"
    assert all(0.0 <= v <= 1.0 for v in u.moments[0].objects[0].box)   # normalized to [0,1]
    assert u.moments[0].ocr == "标语"
    assert u.moments[2].objects == [] and u.moments[2].ocr == ""        # beyond the cap


def test_understand_tunable_grounding_failure_keeps_narrative(tmp_path):
    # a thrown grounding-frame call must degrade that frame only, never lose the narrative moments
    _ffmpeg_or_skip()
    from composerv.devtools import make_cfr_test_clip

    clip = str(tmp_path / "c.mp4")
    make_cfr_test_clip(clip, seconds=8.0, label="A")
    narrative = '{"summary":"s","moments":[{"t":0,"happening":"a"},{"t":0,"happening":"b"}]}'
    calls = {"ground": 0}

    def fake(prompt, imgs):
        if "bbox_2d" in prompt:           # the grounding pass blows up (e.g. the model OOMs)
            calls["ground"] += 1
            raise RuntimeError("model OOM")
        return narrative

    u = understand_clip_tunable(clip, 8.0, run=fake, frames_mode="uniform", max_frames=2,
                                min_frames=2, ground=True, ocr=True, max_ground_frames=2,
                                frames_dir=str(tmp_path / "kf"))
    assert [m.text for m in u.moments] == ["a", "b"]                 # narrative survived the crash
    assert all(m.objects == [] and m.ocr == "" for m in u.moments)  # grounding degraded to empty
    assert calls["ground"] >= 1                                      # the grounding pass was attempted


def test_understand_photo_returns_caption_grounding_ocr(tmp_path):
    from PIL import Image

    from composerv.clarity.understand import understand_photo

    img = str(tmp_path / "p.jpg")
    Image.new("RGB", (400, 300), (120, 120, 120)).save(img)

    def fake(prompt, imgs):
        assert len(imgs) == 1                              # single image, always
        if "bbox_2d" in prompt:
            return '{"objects":[{"label":"person","bbox_2d":[10,10,50,90]}],"ocr":"武夷山"}'
        return '{"caption":"全家在山前合影"}'

    m = understand_photo(img, run=fake, frames_dir=str(tmp_path / "d"))
    assert m.t == 0.0 and m.text == "全家在山前合影" and m.ocr == "武夷山"
    assert m.objects and m.objects[0].label == "person"
    assert all(0.0 <= v <= 1.0 for v in m.objects[0].box)   # boxes normalized to [0,1]


def test_understand_tunable_skips_grounding_by_default(tmp_path):
    _ffmpeg_or_skip()
    from composerv.devtools import make_cfr_test_clip

    clip = str(tmp_path / "c.mp4")
    make_cfr_test_clip(clip, seconds=4.0, label="A")

    def fake(prompt, imgs):
        assert "bbox_2d" not in prompt                 # the grounding pass must NOT run
        return '{"summary":"s","moments":[{"t":0,"happening":"a"}]}'

    u = understand_clip_tunable(clip, 4.0, run=fake, frames_mode="uniform", max_frames=2,
                                min_frames=2, frames_dir=str(tmp_path / "kf"))
    assert all(m.objects == [] and m.ocr == "" for m in u.moments)
