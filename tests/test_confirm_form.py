from composerv.confirm.form import (BriefInput, parse_confirm_submission,
                                    render_confirm_form)
from composerv.faces.review import PersonRow
from composerv.store.db import Brief


def test_render_has_person_inputs_with_prefill():
    rows = [PersonRow(person_id=0, name="小明", sensitive=True, note="我女儿",
                      n_faces=3, n_clips=2, rep_crop="/c/0.jpg")]
    html = render_confirm_form(rows, Brief(scope="s", context="武夷山", style="轻快"),
                               crop_url=lambda pid: f"/crop?id={pid}")
    assert 'name="name_0"' in html and 'value="小明"' in html
    assert 'name="note_0"' in html and 'value="我女儿"' in html
    assert 'name="sensitive_0"' in html and "checked" in html
    assert 'src="/crop?id=0"' in html
    assert 'name="context"' in html and "武夷山" in html
    assert 'name="style"' in html and "轻快" in html
    assert 'action="/save"' in html and 'method="post"' in html


def test_render_no_faces_shows_brief_only():
    html = render_confirm_form([], None, crop_url=lambda pid: "")
    assert "未检测到人脸" in html
    assert 'name="context"' in html and 'name="style"' in html


def test_parse_submission_reads_people_and_brief():
    form = {"name_0": "小明", "sensitive_0": "on", "note_0": "我女儿",
            "name_1": "", "note_1": "", "context": "武夷山家庭游", "style": "轻快"}
    updates, brief = parse_confirm_submission(form)
    by = {u.person_id: u for u in updates}
    assert by[0].name == "小明" and by[0].sensitive is True and by[0].note == "我女儿"
    assert by[1].name == "" and by[1].sensitive is False and by[1].note == ""
    assert brief == BriefInput(context="武夷山家庭游", style="轻快")
