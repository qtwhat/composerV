import pytest
from PIL import Image
from composerv.reframe.track import subject_track, slice_track

def _img(p, w=100, h=200):
    Image.new("RGB", (w, h), (0, 0, 0)).save(p); return str(p)

def test_face_then_person_then_center(tmp_path):
    f0, f1, f2 = _img(tmp_path / "a.jpg"), _img(tmp_path / "b.jpg"), _img(tmp_path / "c.jpg")
    def detect_fn(p):
        return [([40.0, 80.0, 60.0, 120.0], [0.0] * 4)] if p == f0 else []
    out = subject_track([(0.0, f0), (1.0, f1), (2.0, f2)], detect_fn=detect_fn,
                        person_boxes=[(1.1, (0.8, 0.4))])     # nearest-time to t=1.0 within tol
    assert out[0] == (0.0, 0.5, 0.5, 1.0)                      # face center
    assert out[1] == (1.0, 0.8, 0.4, 0.5)                      # person (nearest-time match)
    assert out[2] == (2.0, 0.5, 0.5, 0.0)                      # center

def test_face_pick_prefers_named_gallery_then_continuity(tmp_path):
    f = _img(tmp_path / "m.jpg")
    # two faces: A small at left (cx≈0.2), B large at right (cx≈0.8); target embedding matches A
    def detect_fn(_p):
        return [([10.0, 90.0, 30.0, 110.0], [1.0, 0.0]), ([70.0, 80.0, 95.0, 130.0], [0.0, 1.0])]
    out = subject_track([(0.0, f)], detect_fn=detect_fn, target_centroid=[1.0, 0.0])
    assert abs(out[0][1] - 0.2) < 0.05                         # locked onto A (gallery), not the larger B

def test_slice_track_rebases_to_window():
    tr = [(0.0, .5, .1, 1.0), (9.0, .5, .3, 1.0), (10.0, .5, .5, 1.0), (20.0, .5, .9, 1.0)]
    seg = slice_track(tr, 9.0, 11.0)         # window [8.5, 11.5] catches t=9 and t=10
    times = [t for t, *_ in seg]
    assert times == pytest.approx([0.0, 1.0])   # in-point (t=9) -> 0, t=10 -> 1 (rebased by in_s)
    assert seg[0][1:] == (.5, .3, 1.0)

def test_cos_rejects_dim_mismatch():
    from composerv.reframe.track import _cos
    with pytest.raises(ValueError):
        _cos([1.0, 0.0, 0.0], [1.0, 0.0])
