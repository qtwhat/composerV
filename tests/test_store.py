"""Tests for the SQLite store (assets + captions)."""

import sqlite3

from composerv.analyze.base import CaptionResult
from composerv.index.frames import FrameRef
from composerv.index.probe import MediaInfo
from composerv.store.db import Store


def test_store_migrates_old_persons_and_faces_schema(tmp_path):
    # an older DB whose persons/faces tables predate centroid/n_faces/crop_path
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE persons (person_id INTEGER PRIMARY KEY, name TEXT, sensitive INTEGER);"
        "CREATE TABLE faces (face_id INTEGER PRIMARY KEY AUTOINCREMENT, asset_path TEXT, t REAL,"
        " bbox TEXT, embedding TEXT, person_id INTEGER);"
        "INSERT INTO persons (person_id, name) VALUES (0, 'Old');"
    )
    conn.commit()
    conn.close()

    s = Store(db)  # opening must migrate (add the new columns) without losing data
    p = s.get_person(0)
    assert p.name == "Old" and p.centroid == [] and p.n_faces == 0
    s.set_person_centroid(0, [1.0, 2.0], 3)
    assert s.get_person(0).n_faces == 3
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.replace_faces("/m/a.mp4", [(1.0, [0, 0, 1, 1], [1, 0], "/c/0.jpg")])
    assert s.get_faces("/m/a.mp4")[0].crop_path == "/c/0.jpg"


def test_upsert_and_get_asset(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    mi = MediaInfo(
        path="/x/a.mp4", kind="video", codec="hevc", width=1920, height=1080,
        fps_num=30000, fps_den=1001, has_audio=True, duration_s=3.5,
        capture_time="2025-11-30T07:46:37",
    )
    s.upsert_asset(mi, proxy_path="/tmp/a.proxy.mp4")
    got = s.get_asset("/x/a.mp4")
    assert got is not None
    assert got.codec == "hevc"
    assert (got.width, got.height) == (1920, 1080)
    assert got.proxy_path == "/tmp/a.proxy.mp4"
    assert got.capture_time == "2025-11-30T07:46:37"


def test_upsert_is_idempotent(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    mi = MediaInfo(path="/x/a.mp4", kind="video", codec="hevc")
    s.upsert_asset(mi)
    s.upsert_asset(mi)
    assert len(s.list_assets()) == 1


def test_clip_summary_roundtrip(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    assert s.get_clip_summary("/m/a.mp4") == ""  # none yet
    s.set_clip_summary("/m/a.mp4", "a woman does skincare while her partner watches")
    assert s.get_clip_summary("/m/a.mp4") == "a woman does skincare while her partner watches"
    s.set_clip_summary("/m/a.mp4", "updated")  # overwrites
    assert s.get_clip_summary("/m/a.mp4") == "updated"


def test_upsert_does_not_wipe_clip_summary(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.set_clip_summary("/m/a.mp4", "keep me")
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", codec="hevc"))  # re-scan
    assert s.get_clip_summary("/m/a.mp4") == "keep me"


def test_replace_captions_roundtrip_and_is_idempotent(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/x/a.mp4", kind="video"))
    fr = FrameRef(video_path="/tmp/a.proxy.mp4", index=0, src_pts_s=0.0, image_path="/tmp/f0.jpg")
    cr = CaptionResult(caption="a dog runs", shot_type="wide", objects=["dog"], salience=0.8)

    s.replace_captions("/x/a.mp4", "fake", [(fr, cr)])
    got = s.get_captions("/x/a.mp4")
    assert len(got) == 1
    assert got[0].caption == "a dog runs"
    assert got[0].objects == ["dog"]
    assert got[0].src_pts_s == 0.0
    assert got[0].backend == "fake"

    # re-indexing the same asset+backend replaces, never appends
    s.replace_captions("/x/a.mp4", "fake", [(fr, cr), (fr, cr)])
    assert len(s.get_captions("/x/a.mp4")) == 2


# --- clarity + selection layer ---

def test_clarity_summary_with_source_roundtrip(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    rec = s.get_clarity("/m/a.mp4")
    assert rec.summary == "" and rec.source == "" and rec.selected is False

    s.set_clarity_summary("/m/a.mp4", "a woman does skincare", source="local")
    rec = s.get_clarity("/m/a.mp4")
    assert rec.summary == "a woman does skincare"
    assert rec.source == "local"


def test_refine_overwrites_text_and_source_but_preserves_selection(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.set_clarity_summary("/m/a.mp4", "a woman does skincare", source="local")
    s.set_selected("/m/a.mp4", True)
    s.set_clarity_summary("/m/a.mp4", "a couple does an evening skincare routine", source="claude")
    rec = s.get_clarity("/m/a.mp4")
    assert rec.summary == "a couple does an evening skincare routine"
    assert rec.source == "claude"
    assert rec.selected is True  # a refine must not drop the user's selection


def test_selection_roundtrip_and_list(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    for p in ["/m/a.mp4", "/m/b.mp4", "/m/c.mp4"]:
        s.upsert_asset(MediaInfo(path=p, kind="video"))
    assert s.list_selected() == []
    s.set_selected("/m/a.mp4", True)
    s.set_selected("/m/c.mp4", True)
    assert s.list_selected() == ["/m/a.mp4", "/m/c.mp4"]
    s.set_selected("/m/a.mp4", False)
    assert s.list_selected() == ["/m/c.mp4"]
    # toggling selection preserves an existing summary
    s.set_clarity_summary("/m/c.mp4", "x", source="local")
    s.set_selected("/m/c.mp4", True)
    assert s.get_clarity("/m/c.mp4").summary == "x"


def test_keyframes_roundtrip_and_replace(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    assert s.get_keyframes("/m/a.mp4") == []
    s.set_keyframes("/m/a.mp4", [(0.0, "/t/0.jpg"), (2.0, "/t/1.jpg")])
    assert s.get_keyframes("/m/a.mp4") == [(0.0, "/t/0.jpg"), (2.0, "/t/1.jpg")]
    s.set_keyframes("/m/a.mp4", [(1.0, "/t/x.jpg")])  # replace, never append
    assert s.get_keyframes("/m/a.mp4") == [(1.0, "/t/x.jpg")]


def test_clip_moments_roundtrip_and_replace(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    assert s.get_clip_moments("/m/a.mp4") == []
    s.set_clip_moments("/m/a.mp4", [(0.0, "围着炉子"), (13.2, "特写食物")])
    assert s.get_clip_moments("/m/a.mp4") == [(0.0, "围着炉子"), (13.2, "特写食物")]
    s.set_clip_moments("/m/a.mp4", [(1.0, "新内容")])  # replace, never append
    assert s.get_clip_moments("/m/a.mp4") == [(1.0, "新内容")]


def test_clip_moments_rich_roundtrip_ocr_and_objects(tmp_path):
    from composerv.analyze.clip_video import GroundedObject

    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    assert s.get_clip_moments_rich("/m/a.mp4") == []
    s.set_clip_moments("/m/a.mp4", [
        (0.0, "走进来", "武夷山", [GroundedObject(label="person", box=[0.1, 0.2, 0.3, 0.9])]),
        (5.0, "比耶"),  # short form still allowed -> empty ocr/objects
    ])
    rich = s.get_clip_moments_rich("/m/a.mp4")
    assert [(m.t, m.text, m.ocr) for m in rich] == [(0.0, "走进来", "武夷山"), (5.0, "比耶", "")]
    assert rich[0].objects[0].label == "person" and rich[0].objects[0].box == [0.1, 0.2, 0.3, 0.9]
    assert rich[1].objects == []
    # the plain (t, text) getter is unchanged for existing callers
    assert s.get_clip_moments("/m/a.mp4") == [(0.0, "走进来"), (5.0, "比耶")]


def test_clip_moments_accepts_dict_objects(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.set_clip_moments("/m/a.mp4", [(0.0, "x", "", [{"label": "dog", "box": [0.0, 0.0, 0.5, 0.5]}])])
    rich = s.get_clip_moments_rich("/m/a.mp4")
    assert rich[0].objects[0].label == "dog" and rich[0].objects[0].box == [0.0, 0.0, 0.5, 0.5]


def test_clip_moments_rich_migrates_old_schema(tmp_path):
    # a DB whose clip_moments predates the ocr/objects columns
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE clip_moments (asset_path TEXT NOT NULL, t REAL NOT NULL, text TEXT NOT NULL);"
        "INSERT INTO clip_moments (asset_path, t, text) VALUES ('/m/a.mp4', 1.0, 'old');"
    )
    conn.commit()
    conn.close()
    s = Store(db)  # opening must add the new columns without losing the old row
    rich = s.get_clip_moments_rich("/m/a.mp4")
    assert [(m.t, m.text, m.ocr, m.objects) for m in rich] == [(1.0, "old", "", [])]


def test_set_clip_moments_rejects_short_item_without_wiping(tmp_path):
    import pytest

    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.set_clip_moments("/m/a.mp4", [(0.0, "keep")])
    with pytest.raises(ValueError):
        s.set_clip_moments("/m/a.mp4", [(0.0,)])  # malformed: missing text
    # the DELETE must not run before validation -> previously cached rows survive
    assert s.get_clip_moments("/m/a.mp4") == [(0.0, "keep")]


def test_get_clip_moments_rich_tolerates_corrupt_objects(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.set_clip_moments("/m/a.mp4", [(0.0, "x"), (1.0, "y"), (2.0, "z")])
    # corrupt the objects column out of band, three different ways
    s.conn.execute("UPDATE clip_moments SET objects='{\"k\":1}' WHERE t=0.0")          # JSON object, not array
    s.conn.execute("UPDATE clip_moments SET objects='[{\"label\":\"d\"}]' WHERE t=1.0")  # element missing box
    s.conn.execute("UPDATE clip_moments SET objects='{not json' WHERE t=2.0")          # not JSON at all
    s.conn.commit()
    rich = s.get_clip_moments_rich("/m/a.mp4")  # must not raise
    assert [(m.t, m.text) for m in rich] == [(0.0, "x"), (1.0, "y"), (2.0, "z")]
    assert all(m.objects == [] for m in rich)   # bad boxes skipped, text preserved


def test_transcript_roundtrip_and_replace(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    assert s.get_transcript("/m/a.mp4") == []
    s.set_transcript("/m/a.mp4", [(11.3, 14.1, "这是芋头"), (14.8, 19.5, "拿出来给你们")])
    assert s.get_transcript("/m/a.mp4") == [(11.3, 14.1, "这是芋头"), (14.8, 19.5, "拿出来给你们")]
    s.set_transcript("/m/a.mp4", [(0.0, 1.0, "新句")])
    assert s.get_transcript("/m/a.mp4") == [(0.0, 1.0, "新句")]


def test_clarity_survives_rescan(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.set_clarity_summary("/m/a.mp4", "keep", source="local")
    s.set_selected("/m/a.mp4", True)
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", codec="hevc"))  # re-scan
    rec = s.get_clarity("/m/a.mp4")
    assert rec.summary == "keep" and rec.selected is True


# --- faces + persons ---

def test_faces_roundtrip_all_faces_and_replace(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.upsert_asset(MediaInfo(path="/m/b.mp4", kind="video"))
    s.replace_faces("/m/a.mp4", [(1.0, [0, 0, 10, 10], [0.1, 0.2, 0.3]),
                                 (2.0, [5, 5, 15, 15], [0.4, 0.5, 0.6])])
    s.replace_faces("/m/b.mp4", [(0.5, [1, 1, 9, 9], [0.7, 0.8, 0.9])])
    fa = s.get_faces("/m/a.mp4")
    assert len(fa) == 2
    assert fa[0].t == 1.0 and fa[0].bbox == [0, 0, 10, 10] and fa[0].embedding == [0.1, 0.2, 0.3]
    assert len(s.all_faces()) == 3
    s.replace_faces("/m/a.mp4", [(1.0, [0, 0, 10, 10], [0.1, 0.2, 0.3])])  # idempotent
    assert len(s.get_faces("/m/a.mp4")) == 1


def test_assign_person_and_clip_person_ids(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.replace_faces("/m/a.mp4", [(1.0, [0, 0, 1, 1], [1, 0]), (2.0, [0, 0, 1, 1], [0, 1])])
    faces = s.get_faces("/m/a.mp4")
    s.upsert_person(0)
    s.set_face_person(faces[0].face_id, 0)
    s.upsert_person(1)
    s.set_face_person(faces[1].face_id, 1)
    assert sorted(s.clip_person_ids("/m/a.mp4")) == [0, 1]


def test_person_naming_and_sensitive_flag(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_person(0)
    assert s.get_person(0).name == "" and s.get_person(0).sensitive is False
    s.set_person_name(0, "Grandma", sensitive=True)
    p = s.get_person(0)
    assert p.name == "Grandma" and p.sensitive is True
    assert [x.person_id for x in s.list_persons()] == [0]


def test_person_centroid_persists_and_survives_naming(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_person(0)
    assert s.get_person(0).centroid == [] and s.get_person(0).n_faces == 0
    s.set_person_centroid(0, [0.1, 0.2, 0.3], n_faces=5)  # the growing family gallery
    p = s.get_person(0)
    assert p.centroid == [0.1, 0.2, 0.3] and p.n_faces == 5
    s.set_person_name(0, "Mom")                            # naming must not wipe the centroid
    p = s.get_person(0)
    assert p.centroid == [0.1, 0.2, 0.3] and p.n_faces == 5 and p.name == "Mom"


def test_faces_crop_path_roundtrip_and_optional(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.replace_faces("/m/a.mp4", [(1.0, [0, 0, 1, 1], [1, 0], "/crops/0.jpg")])  # with crop
    assert s.get_faces("/m/a.mp4")[0].crop_path == "/crops/0.jpg"
    s.replace_faces("/m/a.mp4", [(1.0, [0, 0, 1, 1], [1, 0])])                  # 3-tuple still ok
    assert s.get_faces("/m/a.mp4")[0].crop_path == ""


def test_clip_person_names_only_named(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.replace_faces("/m/a.mp4", [(1.0, [0, 0, 1, 1], [1, 0]), (2.0, [0, 0, 1, 1], [0, 1])])
    faces = s.get_faces("/m/a.mp4")
    s.upsert_person(0)
    s.set_face_person(faces[0].face_id, 0)
    s.set_person_name(0, "Mom")
    s.upsert_person(1)
    s.set_face_person(faces[1].face_id, 1)             # unnamed cluster
    assert s.clip_person_names("/m/a.mp4") == ["Mom"]  # only named people surface
    s.set_person_name(1, "Grandma", sensitive=True)
    assert s.clip_person_names("/m/a.mp4") == ["Mom", "Grandma"]
    assert s.clip_person_names("/m/a.mp4", include_sensitive=False) == ["Mom"]  # gate sensitive


def test_reassign_faces_and_delete_person(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.replace_faces("/m/a.mp4", [(1.0, [0, 0, 1, 1], [1, 0]), (2.0, [0, 0, 1, 1], [0, 1])])
    faces = s.get_faces("/m/a.mp4")
    s.upsert_person(0)
    s.set_face_person(faces[0].face_id, 0)
    s.upsert_person(1)
    s.set_face_person(faces[1].face_id, 1)

    s.reassign_faces(1, 0)                              # merge person 1's faces into 0
    assert sorted(s.clip_person_ids("/m/a.mp4")) == [0]
    s.delete_person(1)
    assert s.get_person(1) is None
    assert {p.person_id for p in s.list_persons()} == {0}


def test_named_gallery_lists_known_people_with_centroids(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_person(0)
    s.set_person_name(0, "Mom")
    s.set_person_centroid(0, [1.0, 0.0], n_faces=3)
    s.upsert_person(1)  # an unnamed cluster, no centroid yet
    gallery = s.named_gallery()
    assert gallery == [(0, [1.0, 0.0])]                    # only named people with a centroid


def test_clip_aesthetics_roundtrip_replace_and_default(tmp_path):
    from composerv.store.db import Store
    from composerv.index.probe import MediaInfo

    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    assert s.get_clip_aesthetics("/m/a.mp4") is None           # none yet
    s.set_clip_aesthetics("/m/a.mp4", 2.0, [(0.0, -0.3, True), (2.0, 0.8, False)])
    got = s.get_clip_aesthetics("/m/a.mp4")
    assert got.best_t == 2.0
    assert got.curve == [(0.0, -0.3, True), (2.0, 0.8, False)]
    s.set_clip_aesthetics("/m/a.mp4", None, [(1.0, 0.1, False)])  # replace, never append
    got = s.get_clip_aesthetics("/m/a.mp4")
    assert got.best_t is None and got.curve == [(1.0, 0.1, False)]


def test_reframe_track_roundtrip(tmp_path):
    from composerv.store.db import Store
    s = Store(str(tmp_path / "c.db"))
    track = [(0.0, 0.5, 0.25, 1.0), (1.0, 0.5, 0.75, 0.5)]
    s.set_reframe_track("/m/a.mp4", track)
    assert s.get_reframe_track("/m/a.mp4") == track
    assert s.get_reframe_track("/m/missing.mp4") == []
