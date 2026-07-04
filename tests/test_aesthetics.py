from composerv.analyze.aesthetics import best_moment, distill_quality, quality_tag_at, score_frames
import os


def test_distill_quality_tags_only_the_notable_ends():
    assert distill_quality(0.6, False) == "[清晰·构图好]"
    assert distill_quality(-0.4, False) == "[弱/过渡]"
    assert distill_quality(0.1, True) == "[弱/过渡]"   # isUtility flags filler even at an okay score
    assert distill_quality(0.1, False) == ""           # unremarkable middle: no tag (no prompt noise)
    assert distill_quality(None, False) == ""          # no score -> no tag


def test_best_moment_picks_top_score_excluding_head_and_tail():
    series = [(0.0, 0.9, False), (1.0, 0.3, False), (2.0, 0.7, False), (4.0, 0.95, False)]
    # duration 4.2 -> head/tail 0.3 windows drop t=0.0 and t=4.0; best of the inner is t=2.0
    assert best_moment(series, 4.2) == 2.0


def test_best_moment_returns_none_when_all_weak():
    assert best_moment([(1.0, -0.3, False), (2.0, -0.1, True)], 3.0) is None
    assert best_moment([], 3.0) is None


def test_quality_tag_at_uses_nearest_curve_sample():
    curve = [(0.0, -0.5, False), (2.0, 0.8, False)]
    assert quality_tag_at(0.1, curve) == "[弱/过渡]"   # nearest is t=0.0
    assert quality_tag_at(1.9, curve) == "[清晰·构图好]"  # nearest is t=2.0
    assert quality_tag_at(1.0, []) == ""               # empty curve -> no tag


def test_score_frames_parses_binary_json(tmp_path):
    fake = tmp_path / "fake_aes"
    fake.write_text(
        '#!/bin/sh\n'
        'printf \'[{"path":"a.jpg","score":0.7,"isUtility":false},'
        '{"path":"b.jpg","score":-0.5,"isUtility":true}]\'\n'
    )
    fake.chmod(0o755)
    out = score_frames(["a.jpg", "b.jpg"], binary_path=str(fake))
    assert out == {"a.jpg": (0.7, False), "b.jpg": (-0.5, True)}


def test_score_frames_graceful_when_binary_missing(tmp_path):
    assert score_frames(["a.jpg"], binary_path=str(tmp_path / "nope")) == {}


def test_score_frames_empty_input_is_noop():
    assert score_frames([]) == {}


def test_binary_path_honors_env(monkeypatch):
    from composerv.analyze.aesthetics import _binary_path
    monkeypatch.setenv("CV_AESTHETICS_BIN", "~/somewhere/aesthetics")
    assert _binary_path() == os.path.expanduser("~/somewhere/aesthetics")


def test_binary_path_repo_rooted_when_no_cwd_build(monkeypatch, tmp_path):
    from composerv.analyze import aesthetics
    monkeypatch.delenv("CV_AESTHETICS_BIN", raising=False)
    monkeypatch.chdir(tmp_path)  # no .composerv/bin here
    p = aesthetics._binary_path()
    assert os.path.isabs(p) and p.startswith(aesthetics._REPO_ROOT)
