"""Make sure the scope's clips have detected+clustered faces before the confirm form opens.

ensure_faces is pure-ish (IO injected) so it is unit-tested without insightface;
default_detect_cluster wires the real FaceDetector + enroll helpers for the CLI."""

from __future__ import annotations

from collections.abc import Callable


def ensure_faces(store, paths: list[str], *, detect_fn: Callable[[str], int],
                 cluster_fn: Callable[[], int], log=print) -> tuple[int, int]:
    """Detect faces on clips that have none yet, then (re)cluster. Returns (n_detected, n_people)."""
    detected = 0
    for p in paths:
        if not store.get_faces(p):
            detected += detect_fn(p)
    clusters = cluster_fn()
    return detected, clusters


def default_detect_cluster(store, *, threshold: float = 0.5):
    """Wire the real detector + clusterer (needs insightface). Returns (detect_fn, cluster_fn)."""
    from composerv.faces.detect import FaceDetector
    from composerv.faces.enroll import cluster_all, detect_clip_faces

    detector = FaceDetector()

    def detect_fn(path: str) -> int:
        a = store.get_asset(path)
        proxy = (a.proxy_path or a.path) if a else ""
        dur = a.duration_s if a else 0.0
        return detect_clip_faces(path, proxy, dur, store, detector)

    def cluster_fn() -> int:
        return cluster_all(store, threshold=threshold)

    return detect_fn, cluster_fn
