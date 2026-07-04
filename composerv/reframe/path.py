"""Pure crop-window math for auto-reframe. Coordinates are TOP-LEFT source pixels (+y down),
which matches the AVFoundation layer-instruction transform render space (verified) — so crop
rects feed fill_transform with no sign flip."""
from __future__ import annotations


def cover_window(src_w: int, src_h: int, aspect: float) -> tuple[int, int]:
    if src_w / src_h > aspect:
        return (round(src_h * aspect), src_h)
    return (src_w, round(src_w / aspect))


def clamp_center(cx, cy, win_w, win_h, src_w, src_h):
    hx, hy = win_w / 2, win_h / 2
    return (min(max(cx, hx), src_w - hx), min(max(cy, hy), src_h - hy))


def smooth_centers(targets, *, max_step, dead_zone, ema):
    out, cur = [], list(targets[0]) if targets else [0.0, 0.0]
    for tx, ty in targets:
        nxt = []
        for c, target in ((cur[0], tx), (cur[1], ty)):
            if abs(target - c) < dead_zone:
                nxt.append(c); continue
            step = max(-max_step, min(max_step, (target - c) * ema))
            nxt.append(c + step)
        cur = nxt
        out.append((cur[0], cur[1]))
    return out


def crop_path(track, src_size, aspect, *, fps, max_step_frac=0.06, dead_zone_frac=0.02, ema=0.25):
    src_w, src_h = src_size
    win_w, win_h = cover_window(src_w, src_h, aspect)
    if not track:
        cx, cy = clamp_center(src_w / 2, src_h / 2, win_w, win_h, src_w, src_h)
        return [(0.0, (cx - win_w / 2, cy - win_h / 2, float(win_w), float(win_h)))]
    t0, t1 = track[0][0], track[-1][0]
    n = max(1, round((t1 - t0) * fps) + 1)

    def sample(t):
        best = min(track, key=lambda s: abs(s[0] - t))
        return clamp_center(best[1] * src_w, best[2] * src_h, win_w, win_h, src_w, src_h)

    targets = [sample(t0 + i / fps) for i in range(n)]
    eased = smooth_centers(targets, max_step=max_step_frac * src_h, dead_zone=dead_zone_frac * src_h, ema=ema)
    out = []
    for i, (cx, cy) in enumerate(eased):
        cx, cy = clamp_center(cx, cy, win_w, win_h, src_w, src_h)
        out.append((t0 + i / fps, (cx - win_w / 2, cy - win_h / 2, float(win_w), float(win_h))))
    return out
