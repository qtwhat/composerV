"""Tests for face enroll orchestration (detect on keyframes -> store; cluster -> persons).

The real insightface detector is validated live; here a fake detector exercises the wiring.
"""

import shutil

import pytest

from composerv.faces.enroll import cluster_all, detect_clip_faces, merge_persons
from composerv.index.probe import MediaInfo
from composerv.store.db import Store


def test_detect_clip_faces_samples_and_stores(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    from composerv.devtools import make_cfr_test_clip

    clip = str(tmp_path / "c.mp4")
    make_cfr_test_clip(clip, seconds=8.0, label="A")
    store = Store(str(tmp_path / "db.sqlite"))
    store.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=8.0), proxy_path=clip)

    class FakeDetector:
        def detect(self, path):
            return [([0, 0, 10, 10], [1.0, 0.0]), ([0, 0, 5, 5], [0.0, 1.0])]  # 2 faces/frame

    n = detect_clip_faces("/m/a.mp4", clip, 8.0, store, FakeDetector(),
                          max_frames=4, frames_dir=str(tmp_path / "kf"))
    faces = store.get_faces("/m/a.mp4")
    assert n == len(faces) and n >= 2
    assert all(len(f.embedding) == 2 for f in faces)


def test_cluster_all_assigns_people_across_clips(tmp_path):
    store = Store(str(tmp_path / "db.sqlite"))
    store.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    store.upsert_asset(MediaInfo(path="/m/b.mp4", kind="video"))
    # person A ~ [1,0,0]; person B ~ [0,1,0]; clip b has person A again
    store.replace_faces("/m/a.mp4", [(1.0, [0, 0, 1, 1], [1, 0, 0]), (2.0, [0, 0, 1, 1], [0, 1, 0])])
    store.replace_faces("/m/b.mp4", [(1.0, [0, 0, 1, 1], [0.95, 0.05, 0])])

    n = cluster_all(store, threshold=0.5)
    assert n == 2                                   # two distinct people
    a_people = set(store.clip_person_ids("/m/a.mp4"))
    b_people = set(store.clip_person_ids("/m/b.mp4"))
    assert len(a_people) == 2 and len(b_people) == 1
    assert b_people <= a_people                     # b's person is one of a's two
    assert {p.person_id for p in store.list_persons()} == {0, 1}


def test_named_gallery_auto_recognizes_person_in_new_clip(tmp_path):
    s = Store(str(tmp_path / "db.sqlite"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.upsert_asset(MediaInfo(path="/m/b.mp4", kind="video"))
    # 1) clip a: a face -> cluster -> name it "Dad" (this enriches the gallery)
    s.replace_faces("/m/a.mp4", [(1.0, [0, 0, 100, 200], [1, 0, 0])])
    cluster_all(s, threshold=0.5)
    s.set_person_name(0, "Dad")
    assert s.named_gallery() and s.named_gallery()[0][0] == 0  # Dad is now in the gallery
    # 2) clip b: the same person (similar embedding) is auto-recognized as Dad, no relabeling
    s.replace_faces("/m/b.mp4", [(1.0, [0, 0, 100, 200], [0.95, 0.05, 0])])
    cluster_all(s, threshold=0.5)
    assert 0 in s.clip_person_ids("/m/b.mp4")
    assert s.get_person(0).name == "Dad"          # name preserved through re-clustering
    assert s.get_person(0).n_faces >= 2           # gallery grew with the new face


def test_merge_persons_reassigns_deletes_and_recomputes_centroid(tmp_path):
    s = Store(str(tmp_path / "db.sqlite"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.replace_faces("/m/a.mp4", [(1.0, [0, 0, 1, 1], [1, 0]), (2.0, [0, 0, 1, 1], [0.9, 0.1])])
    faces = s.get_faces("/m/a.mp4")
    s.upsert_person(0)
    s.set_face_person(faces[0].face_id, 0)
    s.upsert_person(1)
    s.set_face_person(faces[1].face_id, 1)
    s.set_person_name(0, "Dad")

    merge_persons(s, [1], 0)                       # person 1 is really Dad too
    assert s.get_person(1) is None                 # merged-away row gone
    assert sorted(s.clip_person_ids("/m/a.mp4")) == [0]
    assert s.get_person(0).name == "Dad"           # the kept person keeps its name
    assert s.get_person(0).n_faces == 2            # centroid recomputed from both faces
    assert len(s.get_person(0).centroid) == 2


def test_cluster_all_skips_small_faces(tmp_path):
    store = Store(str(tmp_path / "db.sqlite"))
    store.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    store.replace_faces("/m/a.mp4", [
        (1.0, [0, 0, 100, 200], [1, 0, 0]),   # foreground face, box height 200 -> kept
        (2.0, [0, 0, 10, 10], [0, 1, 0]),     # tiny background face, height 10 -> skipped
    ])
    n = cluster_all(store, threshold=0.5, min_face_px=50)
    assert n == 1                                   # only the big face becomes a person
    assert store.clip_person_ids("/m/a.mp4") == [0]
    tiny = next(f for f in store.get_faces("/m/a.mp4") if (f.bbox[3] - f.bbox[1]) < 50)
    assert tiny.person_id is None                   # tiny face left unassigned
