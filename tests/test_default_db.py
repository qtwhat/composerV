"""The CLI's default DB lives in the project folder (~/Movies/composerV), and opening a Store at a
not-yet-existing path creates the parent dir (so the default just works on a fresh machine)."""
import os

from composerv.index.probe import MediaInfo
from composerv.render.outputs import default_db
from composerv.store.db import Store


def test_default_db_is_under_output_base(monkeypatch, tmp_path):
    monkeypatch.setenv("CV_OUT", str(tmp_path))
    assert default_db() == os.path.join(str(tmp_path), "composerv.db")


def test_store_creates_missing_parent_dir(tmp_path):
    db = str(tmp_path / "sub" / "deep" / "c.db")   # parent dirs do NOT exist yet
    s = Store(db)
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    assert os.path.exists(db)
    assert Store(db).get_asset("/m/a.mp4") is not None   # reopen: data persisted
