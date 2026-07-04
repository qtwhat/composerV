from typer.testing import CliRunner

from composerv.cli.main import app

runner = CliRunner()


def test_music_index_invokes_indexer_and_reports_count(monkeypatch, tmp_path):
    import composerv.music.features as feat

    monkeypatch.setattr(feat, "index_music_dir", lambda directory, **kw: 3)
    result = runner.invoke(app, ["music", "index", str(tmp_path)])
    assert result.exit_code == 0
    assert "3" in result.stdout
