"""Tests for clarity user actions: resolve a clip id, select/unselect, refine."""

import pytest

from composerv.clarity.actions import refine_clip, resolve_clip, set_selection
from composerv.clarity.summarize import ClaritySummary
from composerv.index.probe import MediaInfo
from composerv.store.db import Store


def _store(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/DJI_A.MP4", kind="video", duration_s=10.0), proxy_path="/px/a.mp4")
    s.upsert_asset(MediaInfo(path="/m/DJI_B.MP4", kind="video", duration_s=5.0), proxy_path="/px/b.mp4")
    return s


def test_resolve_by_basename_and_path(tmp_path):
    s = _store(tmp_path)
    assert resolve_clip(s, "DJI_A.MP4") == "/m/DJI_A.MP4"
    assert resolve_clip(s, "/m/DJI_B.MP4") == "/m/DJI_B.MP4"


def test_resolve_missing_raises(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(KeyError):
        resolve_clip(s, "nope.mp4")


def test_set_selection_toggles_working_set(tmp_path):
    s = _store(tmp_path)
    set_selection(s, ["DJI_A.MP4"], True)
    assert s.list_selected() == ["/m/DJI_A.MP4"]
    set_selection(s, ["DJI_A.MP4"], False)
    assert s.list_selected() == []


def test_refine_clip_uses_proxy_sets_claude_source_keeps_selection(tmp_path):
    s = _store(tmp_path)
    s.set_clarity_summary("/m/DJI_A.MP4", "local desc", source="local")
    s.set_selected("/m/DJI_A.MP4", True)

    seen = {}

    def fake_summarize(proxy, dur):
        seen["proxy"] = proxy
        seen["dur"] = dur
        return ClaritySummary(text="sharper cloud desc", source="claude")

    cs = refine_clip(s, "DJI_A.MP4", summarize=fake_summarize)
    assert cs.text == "sharper cloud desc"
    assert seen["proxy"] == "/px/a.mp4" and seen["dur"] == 10.0
    rec = s.get_clarity("/m/DJI_A.MP4")
    assert rec.summary == "sharper cloud desc" and rec.source == "claude"
    assert rec.selected is True  # refining must not drop the user's selection
