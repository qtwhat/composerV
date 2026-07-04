"""Enroll faces: detect on a clip's keyframes -> store; cluster all faces -> assign people.

Detection is per-clip (uses the motion-aware keyframes). Clustering is global across the
whole library (so the same person is one id everywhere). After clustering, the user names
each person once (see naming/review, separate step).
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable

from composerv.clarity.sampling import select_keyframes
from composerv.faces.cluster import OnlineFaceClusterer
from composerv.store.db import Store


def _save_crop(frame_path: str, bbox: list[float], out_path: str, margin: float = 0.2) -> str:
    """Crop the face (with a little margin) out of the frame and save it; '' on failure."""
    try:
        from PIL import Image

        im = Image.open(frame_path)
        w, h = im.size
        x1, y1, x2, y2 = bbox[:4]
        bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)
        x1 = max(0, int(x1 - bw * margin)); y1 = max(0, int(y1 - bh * margin))
        x2 = min(w, int(x2 + bw * margin)); y2 = min(h, int(y2 + bh * margin))
        if x2 <= x1 or y2 <= y1:
            return ""
        im.crop((x1, y1, x2, y2)).convert("RGB").save(out_path, quality=85)
        return out_path
    except Exception:
        return ""


def detect_clip_faces(
    asset_path: str,
    proxy_path: str,
    duration_s: float,
    store: Store,
    detector,
    *,
    max_frames: int = 12,
    frames_dir: str | None = None,
    mode: str = "motion",
) -> int:
    """Sample keyframes, detect faces, save a crop per face, store (t, bbox, embedding, crop)."""
    frames_dir = frames_dir or tempfile.mkdtemp(prefix="cv_faces_")
    import os

    crop_dir = os.path.join(frames_dir, "crops")
    os.makedirs(crop_dir, exist_ok=True)
    kfs = select_keyframes(proxy_path, frames_dir, duration_s, max_frames=max_frames, mode=mode)
    items: list[tuple] = []
    idx = 0
    for t, path in kfs:
        for bbox, emb in detector.detect(path):
            crop = _save_crop(path, bbox, os.path.join(crop_dir, f"{idx:04d}.jpg"))
            items.append((t, bbox, emb, crop))
            idx += 1
    store.replace_faces(asset_path, items)
    return len(items)


def cluster_all(store: Store, threshold: float = 0.5, min_face_px: float = 0.0) -> int:
    """Cluster every stored face into people (running-mean online clustering), assign each
    face its person id, and ensure a person row exists. Returns the number of people.

    Faces whose bounding-box height is below `min_face_px` are skipped (left unassigned):
    tiny background faces (passers-by) otherwise produce a long tail of singleton 'people'.
    """
    # seed from the persistent gallery of named family so known faces are auto-recognized
    seeds = [(p.person_id, p.centroid, p.n_faces)
             for p in store.list_persons() if p.name and p.centroid]
    clusterer = OnlineFaceClusterer(threshold, seeds=seeds)
    for f in store.all_faces():
        height = (f.bbox[3] - f.bbox[1]) if len(f.bbox) >= 4 else 0.0
        if height < min_face_px:
            store.set_face_person(f.face_id, None)  # too small -> ignore (not a tracked person)
            continue
        pid = clusterer.add(f.embedding)
        store.upsert_person(pid)
        store.set_face_person(f.face_id, pid)
    # persist enriched centroids back to the gallery (named seeds grow; new clusters get one too)
    for cen, cnt, pid in zip(clusterer.centroids, clusterer.counts, clusterer.ids):
        store.set_person_centroid(pid, cen.tolist(), cnt)
    return clusterer.n_clusters


def merge_persons(store: Store, source_ids: list[int], into_id: int) -> None:
    """Merge people (the user said 'these split clusters are the same person'): move all
    source faces to `into_id`, delete the source rows, recompute the merged centroid."""
    import numpy as np

    for sid in source_ids:
        if sid == into_id:
            continue
        store.reassign_faces(sid, into_id)
        store.delete_person(sid)
    embs = [f.embedding for f in store.all_faces() if f.person_id == into_id]
    if embs:
        arr = np.asarray(embs, dtype=np.float64)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        mean = (arr / norms).mean(axis=0)
        n = float(np.linalg.norm(mean))
        cen = (mean / n if n > 0 else mean).tolist()
        store.set_person_centroid(into_id, cen, len(embs))


def enroll_dir_faces(
    store: Store,
    detector,
    *,
    work_dir: str,
    threshold: float = 0.4,
    min_face_px: float = 80.0,
    max_frames: int = 12,
    mode: str = "uniform",
    log: Callable[..., None] = print,
) -> tuple[int, int]:
    """Detect faces for every video asset in the store, then cluster. Returns (n_faces, n_people).

    Defaults (threshold 0.4, min_face_px 80) drop tiny passer-by faces and keep clustering
    conservative (better to over-split and let the user merge than to merge distinct people).
    """
    import os

    total = 0
    for mi in store.list_assets():
        if mi.kind != "video" or not mi.proxy_path:
            continue
        name = os.path.basename(mi.path)
        try:
            n = detect_clip_faces(mi.path, mi.proxy_path, mi.duration_s, store, detector,
                                  max_frames=max_frames, mode=mode,
                                  frames_dir=os.path.join(work_dir, "faces", name))
            total += n
            log(f"{name}: {n} face(s)")
        except Exception as e:
            log(f"FAILED {name}: {e}")
    people = cluster_all(store, threshold=threshold, min_face_px=min_face_px)
    log(f"clustered {total} faces into {people} people (>= {min_face_px:.0f}px)")
    return total, people
