"""CLI smoke tests for the clarity working-set commands (no model/ffmpeg needed)."""

from typer.testing import CliRunner

from composerv.cli.main import app
from composerv.index.probe import MediaInfo
from composerv.store.db import Store

runner = CliRunner()


def test_cli_select_unselect_and_selected(tmp_path):
    db = str(tmp_path / "c.db")
    s = Store(db)
    s.upsert_asset(MediaInfo(path="/m/DJI_A.MP4", kind="video"))
    s.upsert_asset(MediaInfo(path="/m/DJI_B.MP4", kind="video"))

    r = runner.invoke(app, ["select", "DJI_A.MP4", "DJI_B.MP4", "--db", db])
    assert r.exit_code == 0, r.output
    assert "working set now 2" in r.output

    r = runner.invoke(app, ["selected", "--db", db])
    assert "DJI_A.MP4" in r.output and "DJI_B.MP4" in r.output

    r = runner.invoke(app, ["unselect", "DJI_A.MP4", "--db", db])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["selected", "--db", db])
    assert "DJI_A.MP4" not in r.output and "DJI_B.MP4" in r.output


def test_cli_name_and_faces_contactsheet(tmp_path):
    db = str(tmp_path / "c.db")
    s = Store(db)
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    s.replace_faces("/m/a.mp4", [(1.0, [0, 0, 10, 200], [1.0, 0.0], "/c/0.jpg")])
    s.upsert_person(0)
    s.set_face_person(s.get_faces("/m/a.mp4")[0].face_id, 0)

    r = runner.invoke(app, ["name", "0", "Mom", "--db", db])
    assert r.exit_code == 0, r.output
    assert Store(db).get_person(0).name == "Mom"

    out = str(tmp_path / "people.html")
    r = runner.invoke(app, ["faces", "--db", db, "--out", out])
    assert r.exit_code == 0, r.output
    import os
    assert os.path.exists(out) and "Mom" in open(out).read()
