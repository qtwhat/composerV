"""Focus-aware reframe: face-weighted focus point + target-aspect crop math."""

from composerv.render.reframe import _photo_filter, clip_focus, crop_rect, focus_from_objects


def test_focus_is_area_weighted_toward_the_bigger_face():
    # a small face top-left + a big face right -> focus pulled right
    f = clip_focus([(0, 0, 100, 100), (800, 0, 1000, 200)], 1000, 1000)
    assert f is not None and 0.5 < f[0] < 1.0 and 0.0 < f[1] < 0.3


def test_focus_none_without_faces():
    assert clip_focus([], 1920, 1080) is None


def test_crop_landscape_to_3x4_centered():
    # 1920x1080 -> 3:4 (0.75): keep full height, width = 1080*0.75 = 810, centered
    assert crop_rect(1920, 1080, 0.75) == (555, 0, 810, 1080)


def test_crop_portrait_to_3x4_centered():
    # 1080x1920 -> 3:4: keep full width, height = 1080/0.75 = 1440, centered vertically
    assert crop_rect(1080, 1920, 0.75) == (0, 240, 1080, 1440)


def test_crop_follows_focus_and_clamps_to_bounds():
    # focus far left -> window clamps to x=0 (never goes negative / off-frame)
    assert crop_rect(1920, 1080, 0.75, focus=(0.05, 0.5)) == (0, 0, 810, 1080)
    # focus right of centre -> window shifts right
    x, y, w, h = crop_rect(1920, 1080, 0.75, focus=(0.8, 0.5))
    assert x > 555 and x + w <= 1920 and (w, h) == (810, 1080)


def test_focus_from_objects_prefers_people_and_is_area_weighted():
    from composerv.analyze.clip_video import GroundedObject

    objs = [GroundedObject(label="tree", box=[0.0, 0.0, 1.0, 1.0]),
            GroundedObject(label="person", box=[0.6, 0.2, 0.85, 0.95])]
    f = focus_from_objects(objs)
    assert f is not None and f[0] > 0.5     # pulled to the person, not the big background tree
    assert focus_from_objects([]) is None
    # no people -> fall back to all objects (so a scenery photo still gets a focus)
    f2 = focus_from_objects([GroundedObject(label="pagoda", box=[0.1, 0.1, 0.3, 0.4])])
    assert f2 is not None and f2[0] < 0.5


def test_photo_filter_static_crops_and_scales_no_zoom():
    f = _photo_filter((10, 20, 300, 400), 1080, 1440, 3.0, "static")
    assert "crop=300:400:10:20" in f and "scale=1080:1440" in f and "zoompan" not in f


def test_photo_filter_kenburns_in_and_out():
    fin = _photo_filter((0, 0, 300, 400), 1080, 1440, 3.0, "in")
    assert "zoompan" in fin and "zoom+" in fin and "1080x1440" in fin
    fout = _photo_filter((0, 0, 300, 400), 1080, 1440, 3.0, "out")
    assert "zoompan" in fout and "zoom-" in fout
