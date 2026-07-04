"""Tests for online face clustering (pure: running-mean centroids + cosine threshold)."""

import numpy as np

from composerv.faces.cluster import OnlineFaceClusterer


def test_same_person_clusters_together_others_separate():
    c = OnlineFaceClusterer(threshold=0.5)
    a1 = c.add([1, 0, 0, 0])
    a2 = c.add([0.9, 0.1, 0, 0])   # very similar -> same person
    b = c.add([0, 0, 1, 0])        # orthogonal -> different person
    assert a1 == a2
    assert b != a1
    assert c.n_clusters == 2


def test_threshold_controls_merge_vs_split():
    e1, e2 = [1, 0, 0, 0], [0.6, 0.4, 0, 0]  # cosine similarity ~0.83
    loose = OnlineFaceClusterer(threshold=0.5)
    assert loose.add(e1) == loose.add(e2)    # merges under a loose threshold
    strict = OnlineFaceClusterer(threshold=0.95)
    assert strict.add(e1) != strict.add(e2)  # splits under a strict threshold


def test_running_mean_centroid_moves_toward_members_and_stays_unit():
    c = OnlineFaceClusterer(threshold=0.3)
    cid = c.add([1.0, 0, 0, 0])
    c.add([0.7, 0.7, 0, 0])                   # merges; pulls the centroid toward axis 1
    cen = np.asarray(c.centroids[cid])
    assert cen[1] > 0.0
    assert abs(float(np.linalg.norm(cen)) - 1.0) < 1e-6  # centroid kept normalized


def test_add_returns_stable_ids():
    c = OnlineFaceClusterer(threshold=0.5)
    first = c.add([1, 0, 0, 0])
    c.add([0, 1, 0, 0])                        # a second person
    again = c.add([0.95, 0.05, 0, 0])          # back to the first person
    assert again == first


def test_unseeded_ids_start_at_zero():
    c = OnlineFaceClusterer(threshold=0.5)
    assert c.add([1, 0, 0, 0]) == 0
    assert c.add([0, 1, 0, 0]) == 1


def test_seeded_clusterer_recognizes_known_person():
    # a known family member: person 5 with a stored centroid ~ [1,0,0]
    c = OnlineFaceClusterer(threshold=0.5, seeds=[(5, [1, 0, 0])])
    assert c.add([0.95, 0.05, 0]) == 5          # auto-recognized as the known person
    new = c.add([0, 1, 0])                       # a stranger -> a fresh id past the seeds
    assert new != 5 and new >= 6
    assert c.add([0.05, 0.97, 0]) == new        # second stranger face joins the new id
