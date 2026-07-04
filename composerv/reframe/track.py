"""Assemble a dense subject-center track: named-face (gallery) -> any-face (continuity) -> person
(nearest-time) -> center. Pure given an injected detect_fn that returns [(bbox, embedding)]."""
from __future__ import annotations
import math
from bisect import bisect_left


def _img_size(path):
    from PIL import Image
    with Image.open(path) as im:
        return im.size


def _cos(a, b):
    s = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return s / (na * nb) if na and nb else 0.0


def _center_norm(box, w, h):
    return ((box[0] + box[2]) / 2 / w, (box[1] + box[3]) / 2 / h)


def _pick_face(faces, w, h, *, prev=None, target_centroid=None, lock_thresh=0.4):
    # 0.4 == the project's enroll/cluster same-person threshold (faces/enroll.py, cluster.py);
    # a looser lock could capture a DIFFERENT person's face. Keep in sync with enrollment.
    cands = [(_center_norm(b, w, h), b, e) for b, e in faces]
    if target_centroid is not None:
        c, score = max(((c, _cos(e, target_centroid)) for c, _b, e in cands), key=lambda m: m[1])
        if score >= lock_thresh:
            return c
    if prev is not None:
        return min(cands, key=lambda c: (c[0][0] - prev[0]) ** 2 + (c[0][1] - prev[1]) ** 2)[0]
    return max(cands, key=lambda c: (c[1][2] - c[1][0]) * (c[1][3] - c[1][1]))[0]


def _nearest_person(ts, cs, t, tol):
    if not ts:
        return None
    i = bisect_left(ts, t)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(ts):
            d = abs(ts[j] - t)
            if d <= tol and (best is None or d < best[0]):
                best = (d, cs[j])
    return best[1] if best else None


def subject_track(frame_samples, *, detect_fn, person_boxes=None, target_centroid=None, person_tol=0.6):
    pb = sorted(person_boxes or [])
    ts, cs = [t for t, _ in pb], [c for _, c in pb]
    out, prev = [], None
    for t, path in frame_samples:
        faces = detect_fn(path) or []
        if faces:
            w, h = _img_size(path)
            cx, cy = _pick_face(faces, w, h, prev=prev, target_centroid=target_centroid)
            out.append((t, cx, cy, 1.0)); prev = (cx, cy)
        else:
            pc = _nearest_person(ts, cs, t, person_tol)
            if pc is not None:
                out.append((t, float(pc[0]), float(pc[1]), 0.5)); prev = (float(pc[0]), float(pc[1]))
            else:
                out.append((t, 0.5, 0.5, 0.0))   # keep prev: a brief gap shouldn't snap to center
    return out


def slice_track(track, in_s, out_s, *, pad=0.5):
    seg = [(t - in_s, cx, cy, c) for (t, cx, cy, c) in track if in_s - pad <= t <= out_s + pad]
    return seg or [(0.0, 0.5, 0.5, 0.0)]
