from composerv.index.probe import MediaInfo
from composerv.store.db import Store


def _named_clip(tmp_path, name="小明", note="我女儿"):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.replace_faces("/m/a.mp4", [(1.0, [0, 0, 10, 100], [1, 0], "/c/a0.jpg")])
    face = s.get_faces("/m/a.mp4")[0]
    s.upsert_person(0)
    s.set_face_person(face.face_id, 0)
    s.set_person_name(0, name)
    if note:
        s.set_person_note(0, note)
    return s


def test_person_note_roundtrip(tmp_path):
    s = _named_clip(tmp_path)
    assert s.get_person(0).note == "我女儿"


def test_clip_person_labels_appends_note(tmp_path):
    s = _named_clip(tmp_path)
    assert s.clip_person_labels("/m/a.mp4") == ["小明（我女儿）"]


def test_clip_person_labels_plain_when_no_note(tmp_path):
    s = _named_clip(tmp_path, note="")
    assert s.clip_person_labels("/m/a.mp4") == ["小明"]


def test_set_person_name_preserves_note(tmp_path):
    s = _named_clip(tmp_path)
    s.set_person_name(0, "小明", sensitive=True)   # renaming must not wipe the note
    assert s.get_person(0).note == "我女儿" and s.get_person(0).sensitive is True


def test_brief_roundtrip_by_scope(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    assert s.get_brief("2025-12-14") is None
    s.set_brief("2025-12-14", context="武夷山家庭游", style="轻快，多留孩子的镜头")
    b = s.get_brief("2025-12-14")
    assert b.context == "武夷山家庭游" and b.style == "轻快，多留孩子的镜头"


def test_brief_update_and_scope_isolation(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.set_brief("2025-12-14", context="旧", style="旧风格")
    s.set_brief("2025-12-14", context="新", style="")
    assert s.get_brief("2025-12-14").context == "新" and s.get_brief("2025-12-14").style == ""
    assert s.get_brief("selected") is None   # keyed by scope, independent
