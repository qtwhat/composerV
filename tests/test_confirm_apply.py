from composerv.confirm.apply import apply_submission
from composerv.confirm.form import BriefInput, PersonUpdate
from composerv.store.db import Store


def test_apply_writes_names_notes_and_brief(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_person(0)
    apply_submission(s, "2025-12-14",
                     [PersonUpdate(person_id=0, name="小明", sensitive=True, note="我女儿")],
                     BriefInput(context="武夷山", style="轻快"))
    p = s.get_person(0)
    assert p.name == "小明" and p.sensitive is True and p.note == "我女儿"
    b = s.get_brief("2025-12-14")
    assert b.context == "武夷山" and b.style == "轻快"
