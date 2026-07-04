from composerv.reframe.path import cover_window, smooth_centers, crop_path

def test_cover_window_portrait_full_width():
    assert cover_window(1080, 1920, 16 / 9) == (1080, 608)        # 1080*9/16=607.5->608

def test_cover_window_landscape_full_height():
    assert cover_window(2400, 1080, 16 / 9) == (1920, 1080)

def test_smooth_centers_eases_within_speed_cap():
    out = smooth_centers([(0.0, 100.0)] + [(0.0, 500.0)] * 20, max_step=50.0, dead_zone=2.0, ema=0.5)
    ys = [y for _x, y in out]
    assert max(abs(ys[i] - ys[i - 1]) for i in range(1, len(ys))) <= 50.0 + 1e-6
    assert ys[0] == 100.0 and abs(ys[-1] - 500.0) < 2.0

def test_smooth_centers_dead_zone_holds_on_jitter():
    out = smooth_centers([(0.0, 100.0), (0.0, 101.0), (0.0, 99.5)], max_step=50.0, dead_zone=2.0, ema=0.5)
    assert all(abs(y - 100.0) < 1e-9 for _x, y in out)

def test_crop_path_window_slides_down_to_follow_a_lower_face_and_stays_in_bounds():
    track = [(0.0, 0.5, 0.25, 0.9), (2.0, 0.5, 0.75, 0.9)]        # face high -> low
    path = crop_path(track, (1080, 1920), 16 / 9, fps=30)
    assert len(path) == 61
    for _t, (x, y, w, h) in path:
        assert (w, h) == (1080, 608) and x == 0.0 and 0.0 <= y <= 1920 - 608
    assert path[-1][1][1] > path[0][1][1]                          # window moved DOWN (top-left +y down)
