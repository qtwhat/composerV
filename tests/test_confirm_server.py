import urllib.parse
import urllib.request

from composerv.confirm.server import make_confirm_server
from composerv.index.probe import MediaInfo
from composerv.store.db import Store


def test_server_get_form_then_post_saves(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.replace_faces("/m/a.mp4", [(1.0, [0, 0, 10, 100], [1, 0], "")])
    s.upsert_person(0)
    s.set_face_person(s.get_faces("/m/a.mp4")[0].face_id, 0)

    srv, url, done = make_confirm_server(s, "2025-12-14")
    try:
        html = urllib.request.urlopen(url, timeout=5).read().decode("utf-8")
        assert 'name="name_0"' in html and 'name="context"' in html
        body = urllib.parse.urlencode({"name_0": "小明", "sensitive_0": "on", "note_0": "我女儿",
                                       "context": "武夷山", "style": "轻快"}).encode()
        resp = urllib.request.urlopen(url + "save", data=body, timeout=5)
        assert resp.status == 200
    finally:
        srv.shutdown()

    assert s.get_person(0).name == "小明" and s.get_person(0).note == "我女儿"
    assert s.get_brief("2025-12-14").context == "武夷山"
    assert done.is_set()
