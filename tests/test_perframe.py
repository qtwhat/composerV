"""Per-frame v2 understanding: terse-Chinese, people-first single-image VLM pass.

Pure parts (prompt builder, parser) tested directly; the per-frame orchestration tested with an
injected run, like the rest of the VLM code (real model validated live).
"""
import shutil

import pytest

from composerv.analyze.backends.qwen_mlx import build_perframe_prompt, parse_perframe
from composerv.analyze.clip_video import ClipUnderstanding
from composerv.clarity.understand import understand_clip_perframe


def test_build_perframe_prompt_carries_timestamp_and_people_first_rule():
    p = build_perframe_prompt(52.0)
    assert "52" in p                       # the frame's own second is stated
    assert "一句" in p                      # one terse line
    assert "表情" in p and "视线" in p      # must report expression + gaze
    assert "不写风景" in p                   # people-first: do not fall back to scenery
    assert "serene" in p or "禁止" in p     # banned-filler instruction
    assert '"d"' in p                       # the single-line JSON output shape


def test_parse_perframe_json_object():
    assert parse_perframe('{"t":52,"d":"女人微笑看镜头"}') == "女人微笑看镜头"


def test_parse_perframe_bare_line():
    assert parse_perframe("男子蹲着拍照，专注") == "男子蹲着拍照，专注"


def test_parse_perframe_empty_and_wu_become_empty():
    assert parse_perframe("无") == ""
    assert parse_perframe("") == ""
    assert parse_perframe('{"t":5,"d":"无"}') == ""


def test_parse_perframe_strips_code_fence():
    assert parse_perframe('```json\n{"t":0,"d":"蓝色碗里有鸡蛋"}\n```') == "蓝色碗里有鸡蛋"


def test_understand_clip_perframe_single_image_real_times_skips_empty(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    from composerv.devtools import make_cfr_test_clip

    clip = str(tmp_path / "c.mp4")
    make_cfr_test_clip(clip, seconds=8.0, label="A")
    n_imgs = []
    calls = {"i": 0}

    def fake(prompt, imgs):
        n_imgs.append(len(imgs))
        calls["i"] += 1
        if calls["i"] == 2:                      # 2nd frame (t=2.0): model returns empty -> skip it
            return "无"
        return '{"t":0,"d":"有人在笑"}'

    u = understand_clip_perframe(clip, 8.0, run=fake, frames_mode="uniform",
                                 max_frames=4, min_frames=4, frames_dir=str(tmp_path / "kf"))
    assert isinstance(u, ClipUnderstanding)
    assert all(n == 1 for n in n_imgs)           # ONE image per call (per-frame, not multi-image)
    assert len(n_imgs) == 4                       # all 4 frames were visited
    assert [m.t for m in u.moments] == [0.0, 4.0, 6.0]   # t=2.0 empty -> dropped; real frame times
    assert all(m.text == "有人在笑" for m in u.moments)


def test_understand_clip_perframe_grounds_and_ocrs_first_kept_frames(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    from composerv.devtools import make_cfr_test_clip

    clip = str(tmp_path / "c.mp4")
    make_cfr_test_clip(clip, seconds=8.0, label="A")

    def fake(prompt, imgs):
        assert len(imgs) == 1                                  # single image always
        if "bbox_2d" in prompt:                                # the per-frame grounding pass
            return '{"objects":[{"label":"person","bbox_2d":[10,10,50,90]}],"ocr":"茶园"}'
        return '{"t":0,"d":"有人在笑"}'                          # the per-frame narrative pass

    u = understand_clip_perframe(clip, 8.0, run=fake, frames_mode="uniform", max_frames=4,
                                 min_frames=4, ground=True, ocr=True, max_ground_frames=2,
                                 frames_dir=str(tmp_path / "kf"))
    assert u.moments[0].objects and u.moments[0].objects[0].label == "person"
    assert all(0.0 <= v <= 1.0 for v in u.moments[0].objects[0].box)   # boxes normalized to [0,1]
    assert u.moments[0].ocr == "茶园"
    assert u.moments[2].objects == [] and u.moments[2].ocr == ""        # beyond the cap
