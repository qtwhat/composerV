from composerv.confirm.enroll_glue import ensure_faces
from composerv.index.probe import MediaInfo
from composerv.store.db import Store


def test_ensure_faces_detects_only_missing_then_clusters(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    for p in ["/m/a.mp4", "/m/b.mp4"]:
        s.upsert_asset(MediaInfo(path=p, kind="video"))
    s.replace_faces("/m/a.mp4", [(1.0, [0, 0, 10, 100], [1, 0], "")])  # a already has faces

    detected = []
    d, c = ensure_faces(
        s, ["/m/a.mp4", "/m/b.mp4"],
        detect_fn=lambda p: (detected.append(p) or 1),
        cluster_fn=lambda: 7,
    )
    assert detected == ["/m/b.mp4"]   # only the clip lacking faces is detected
    assert d == 1 and c == 7
