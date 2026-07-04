"""Tests for the clarity catalog: pure HTML render + store->cards builder."""

from composerv.clarity.catalog import ClarityCard, build_cards, render_catalog
from composerv.index.probe import MediaInfo
from composerv.store.db import Store


def test_render_catalog_shows_summary_id_keyframes_and_facts():
    cards = [
        ClarityCard(clip_id="A.mp4", path="/m/A.mp4", summary="a woman does skincare",
                    source="local", selected=True, duration_s=34.2,
                    capture_time="2025-11-30T07:46:37", keyframes=["/t/a0.jpg", "/t/a1.jpg"]),
        ClarityCard(clip_id="B.mp4", path="/m/B.mp4", summary="", source="", selected=False,
                    duration_s=8.0, capture_time="2025-12-14T14:42:47", keyframes=["/t/b0.jpg"]),
    ]
    html = render_catalog(cards, title="DJI")
    assert "<html" in html.lower() and "DJI" in html
    assert "a woman does skincare" in html          # summary
    assert "A.mp4" in html and "B.mp4" in html      # clip ids (for CLI select/refine)
    assert 'src="/t/a0.jpg"' in html and 'src="/t/a1.jpg"' in html  # keyframe imgs
    assert "local" in html                          # source label
    assert "✓" in html                              # selected marker (A is selected)
    assert "34" in html                             # duration shown
    assert "2025-11-30" in html                     # capture date
    assert "no description" in html.lower()         # B's empty summary -> placeholder


def test_render_catalog_has_archive_overview_and_day_counts():
    cards = [
        ClarityCard(clip_id="a.mp4", path="/a.mp4", capture_time="2025-11-30T07:00:00"),
        ClarityCard(clip_id="b.mp4", path="/b.mp4", capture_time="2025-12-14T18:00:00"),
        ClarityCard(clip_id="c.mp4", path="/c.mp4", capture_time="2025-12-14T19:00:00"),
    ]
    html = render_catalog(cards)
    assert "3 clips" in html                 # total at a glance
    assert "across 2 days" in html           # date span / day count
    assert "2025-11-30" in html and "2025-12-14" in html
    assert "(2)" in html and "(1)" in html   # per-day tallies (12-14 has 2, 11-30 has 1)


def test_render_catalog_groups_by_day_sorted_ascending():
    cards = [
        ClarityCard(clip_id="late.mp4", path="/m/late.mp4", capture_time="2025-12-14T18:00:00"),
        ClarityCard(clip_id="early.mp4", path="/m/early.mp4", capture_time="2025-11-30T07:00:00"),
    ]
    html = render_catalog(cards)
    assert html.find("2025-11-30") < html.find("2025-12-14")   # earliest day first
    assert html.find("early.mp4") < html.find("late.mp4")


def test_render_catalog_escapes_html():
    cards = [ClarityCard(clip_id="x.mp4", path="/m/x.mp4", summary='a <b> & "q"')]
    html = render_catalog(cards)
    assert "&lt;b&gt;" in html and "&amp;" in html
    assert "<b>" not in html.split("a ", 1)[1][:20]  # the summary's < was escaped


def test_render_catalog_shows_named_people():
    cards = [ClarityCard(clip_id="A.mp4", path="/m/A.mp4", summary="a walk",
                         people=["Mom", "哥哥"])]
    html = render_catalog(cards)
    assert "Mom" in html and "哥哥" in html


def test_build_cards_includes_named_people(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/DJI_A.MP4", kind="video"))
    s.replace_faces("/m/DJI_A.MP4", [(1.0, [0, 0, 1, 1], [1, 0])])
    f = s.get_faces("/m/DJI_A.MP4")[0]
    s.upsert_person(0)
    s.set_face_person(f.face_id, 0)
    s.set_person_name(0, "哥哥")
    assert build_cards(s)[0].people == ["哥哥"]


def test_build_cards_from_store(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/DJI_A.MP4", kind="video", duration_s=12.0,
                             capture_time="2025-11-30T07:00:00"))
    s.set_clarity_summary("/m/DJI_A.MP4", "a drone shot over water", source="local")
    s.set_selected("/m/DJI_A.MP4", True)
    s.set_keyframes("/m/DJI_A.MP4", [(0.0, "/t/0.jpg"), (6.0, "/t/1.jpg")])

    cards = build_cards(s)
    assert len(cards) == 1
    c = cards[0]
    assert c.clip_id == "DJI_A.MP4"
    assert c.path == "/m/DJI_A.MP4"
    assert c.summary == "a drone shot over water"
    assert c.selected is True and c.source == "local"
    assert c.duration_s == 12.0
    assert c.keyframes == ["/t/0.jpg", "/t/1.jpg"]
