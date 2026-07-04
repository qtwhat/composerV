"""Tests for the face naming/review surface (recurrence ranking + HTML contact sheet)."""

from composerv.faces.review import PersonRow, person_rows, render_face_contactsheet
from composerv.index.probe import MediaInfo
from composerv.store.db import Store


def _assign(store, asset, faces_by_pid):
    """faces_by_pid: list[(person_id, face_index_in_clip)] using current get_faces order."""
    faces = store.get_faces(asset)
    for pid, idx in faces_by_pid:
        store.upsert_person(pid)
        store.set_face_person(faces[idx].face_id, pid)


def test_person_rows_ranked_by_recurrence_with_representative_crop(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    for p in ["/m/a.mp4", "/m/b.mp4", "/m/c.mp4"]:
        s.upsert_asset(MediaInfo(path=p, kind="video"))
    s.replace_faces("/m/a.mp4", [(1.0, [0, 0, 10, 200], [1, 0], "/c/a0.jpg")])
    s.replace_faces("/m/b.mp4", [(1.0, [0, 0, 10, 100], [1, 0], "/c/b0.jpg")])
    s.replace_faces("/m/c.mp4", [(1.0, [0, 0, 10, 300], [1, 0], "/c/c0.jpg"),
                                 (2.0, [0, 0, 10, 10], [0, 1], "/c/c1.jpg")])
    _assign(s, "/m/a.mp4", [(0, 0)])
    _assign(s, "/m/b.mp4", [(0, 0)])
    _assign(s, "/m/c.mp4", [(0, 0), (1, 1)])

    rows = person_rows(s)
    assert rows[0].person_id == 0 and rows[0].n_clips == 3   # most-recurring first
    assert rows[0].rep_crop == "/c/c0.jpg"                   # largest face (h=300) represents them
    assert rows[1].person_id == 1 and rows[1].n_clips == 1


def test_person_rows_min_clips_filter(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.replace_faces("/m/a.mp4", [(1.0, [0, 0, 10, 200], [1, 0], "/c/a0.jpg"),
                                 (2.0, [0, 0, 10, 100], [0, 1], "/c/a1.jpg")])
    _assign(s, "/m/a.mp4", [(0, 0), (1, 1)])
    assert [r.person_id for r in person_rows(s, min_clips=1)] == [0, 1]
    assert person_rows(s, min_clips=2) == []   # nobody recurs across >=2 clips here


def test_person_rows_forwards_stored_note(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.replace_faces("/m/a.mp4", [(1.0, [0, 0, 10, 100], [1, 0], "/c/a0.jpg")])
    _assign(s, "/m/a.mp4", [(0, 0)])
    s.set_person_name(0, "小明")
    s.set_person_note(0, "我女儿")
    rows = person_rows(s)
    assert rows[0].note == "我女儿"


def test_render_contactsheet_shows_face_name_id_and_unnamed():
    rows = [
        PersonRow(person_id=0, name="Mom", n_faces=5, n_clips=3, rep_crop="/c/0.jpg"),
        PersonRow(person_id=1, name="", n_faces=1, n_clips=1, rep_crop="/c/1.jpg"),
    ]
    html = render_face_contactsheet(rows, title="People")
    assert "People" in html
    assert "Mom" in html and 'src="/c/0.jpg"' in html
    assert "unnamed" in html.lower()                 # person 1 has no name yet
    assert "3 clips" in html                          # recurrence shown
    assert "name 0" in html or "id 0" in html or "#0" in html  # the id to type into the CLI
