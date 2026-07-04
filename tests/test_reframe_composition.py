"""Pure-function tests for clip_layout, fit_transform, fill_transform in composition.py."""
from composerv.render.preview.composition import clip_layout, fit_transform, fill_transform


def test_clip_layout_frame_snapped_cursor():
    edl = [{"kind": "clip", "file": "a", "in": 0.0, "out": 1.04},   # 1.04s @30fps -> 31 frames
           {"kind": "gap", "duration": 1.0},
           {"kind": "clip", "file": "b", "in": 2.0, "out": 4.0}]
    lay = clip_layout(edl, fps=30)
    assert lay[0][2]["file"] == "a" and abs(lay[0][1] - 31 / 30) < 1e-9 and lay[0][0] == 0.0
    # start of b = (31 frames + 30 gap frames) / 30
    assert abs(lay[1][0] - 61 / 30) < 1e-9 and lay[1][2]["file"] == "b"


def test_fit_transform_centers_portrait():
    s, tx, ty = fit_transform(1080, 1920, 1280, 720)
    assert abs(s - 0.375) < 1e-6 and abs(ty) < 1e-6 and abs(tx - (1280 - 1080 * 0.375) / 2) < 1e-6


def test_fill_transform_no_sign_flip_and_face_low_pushes_content_up():
    # crop low (y=1136) must give ty MORE negative than crop high (y=176): verified render semantics
    _, _, ty_hi = fill_transform((0.0, 176.0, 1080.0, 608.0), 1280, 720)
    _, _, ty_lo = fill_transform((0.0, 1136.0, 1080.0, 608.0), 1280, 720)
    assert ty_hi == -176.0 * (1280 / 1080) and ty_lo == -1136.0 * (1280 / 1080) and ty_lo < ty_hi
