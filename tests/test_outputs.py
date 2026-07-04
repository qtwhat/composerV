"""Outputs go to a persistent tree organized by type then date (never /tmp)."""

import os

from composerv.render.outputs import out_path, output_base


def test_out_path_organizes_by_type_and_date(tmp_path):
    p = out_path("mp4", "reel.lite.mp4", base=str(tmp_path), date="2026-06-22")
    assert p == os.path.join(str(tmp_path), "mp4", "2026-06-22", "reel.lite.mp4")
    assert os.path.isdir(os.path.dirname(p))   # directory created


def test_output_base_honors_cv_out(monkeypatch):
    monkeypatch.setenv("CV_OUT", "/x/out")
    assert output_base() == "/x/out"


def test_output_base_default(monkeypatch):
    monkeypatch.delenv("CV_OUT", raising=False)
    assert output_base() == os.path.expanduser("~/Movies/composerV")


def test_default_music_dir_honors_cv_music_dir(monkeypatch):
    monkeypatch.setenv("CV_MUSIC_DIR", "/x/music")
    from composerv.render.outputs import default_music_dir
    assert default_music_dir() == "/x/music"


def test_default_music_dir_default(monkeypatch):
    monkeypatch.delenv("CV_MUSIC_DIR", raising=False)
    from composerv.render.outputs import default_music_dir
    assert default_music_dir() == os.path.expanduser("~/.composerv/music")
