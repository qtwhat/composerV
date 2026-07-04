from typer.testing import CliRunner

from composerv.cli.main import app
from composerv.index.probe import MediaInfo
from composerv.store.db import Store


def test_confirm_cli_no_detect_serves(tmp_path, monkeypatch):
    db = str(tmp_path / "c.db")
    Store(db).upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"), proxy_path="/p/a.mp4")

    served = {}
    monkeypatch.setattr("composerv.confirm.server.serve_confirm",
                        lambda store, scope, **k: served.update(scope=scope) or "http://x/")

    r = CliRunner().invoke(app, ["confirm", "all", "--db", db, "--no-detect"])
    assert r.exit_code == 0, r.output
    assert served.get("scope") == "all"
