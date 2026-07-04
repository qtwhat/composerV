"""Online face clustering: running-mean centroids + a cosine-similarity threshold.

Unsupervised and incremental: each new face joins the most similar existing person if the
cosine similarity clears the threshold, else it starts a new person. No global pass needed,
so the library can grow clip by clip. Pure (numpy only) -> fully unit-testable.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def _normalize(v: Sequence[float]) -> np.ndarray:
    a = np.asarray(v, dtype=np.float64)
    n = float(np.linalg.norm(a))
    return a / n if n > 0 else a


class OnlineFaceClusterer:
    """Assign each embedding to a person id (cluster). `threshold` is cosine similarity:
    higher = stricter (more, tighter clusters); ~0.4-0.5 works for insightface embeddings."""

    def __init__(self, threshold: float = 0.5,
                 seeds: list[tuple[int, Sequence[float]]] | None = None):
        """`seeds` are known people (person_id, centroid) from the persistent gallery: a new
        face that matches a seed is recognized as that person; unmatched faces get fresh ids
        past the seeds. With no seeds, ids start at 0 (plain clustering)."""
        self.threshold = threshold
        self.centroids: list[np.ndarray] = []  # unit vectors
        self.counts: list[int] = []
        self.ids: list[int] = []               # person_id for each cluster (parallel to centroids)
        for item in (seeds or []):
            pid, cen = item[0], item[1]
            count = item[2] if len(item) > 2 else 1  # carry the gallery count so the mean stays weighted
            self.centroids.append(_normalize(cen))
            self.counts.append(int(count))
            self.ids.append(pid)
        self._next_id = (max(self.ids) + 1) if self.ids else 0

    @property
    def n_clusters(self) -> int:
        return len(self.centroids)

    def add(self, embedding: Sequence[float]) -> int:
        e = _normalize(embedding)
        if self.centroids:
            sims = [float(np.dot(e, c)) for c in self.centroids]
            best = int(np.argmax(sims))
            if sims[best] >= self.threshold:
                n = self.counts[best]
                self.centroids[best] = _normalize(self.centroids[best] * n + e)
                self.counts[best] = n + 1
                return self.ids[best]
        self.centroids.append(e)
        self.counts.append(1)
        new_id = self._next_id
        self.ids.append(new_id)
        self._next_id += 1
        return new_id
