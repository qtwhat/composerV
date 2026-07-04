# composerv/reframe/detect.py
"""Live subject track over a clip window. Validated manually."""
from __future__ import annotations
import tempfile
from composerv.index.frames import sample_frames
from composerv.reframe.track import subject_track

_DETECTOR = None
_DETECTOR_SIZE = None


def _detector(det_size=(384, 384)):
    # Cache one FaceDetector; rebuild it if the requested det_size changes
    # (insightface init is ~1s, so reuse the instance across same-size calls).
    global _DETECTOR, _DETECTOR_SIZE
    if _DETECTOR is None or _DETECTOR_SIZE != det_size:
        from composerv.faces.detect import FaceDetector
        _DETECTOR = FaceDetector(det_size=det_size)
        _DETECTOR_SIZE = det_size
    return _DETECTOR


def compute_track(video_path, in_s, out_s, *, person_boxes=None, target_centroid=None,
                  fps=4.0, det_size=(384, 384), progress=None):
    dur = max(0.0, float(out_s) - float(in_s))
    det = _detector(det_size)
    # Extract frames into a temp dir removed once the track is built — the detector reads each
    # JPEG during subject_track, so nothing needs them after this returns. (Without cleanup every
    # export orphaned ~100-300MB of frames in $TMPDIR, accumulating across runs.)
    with tempfile.TemporaryDirectory(prefix="cv_reframe_") as frame_dir:
        fr = sample_frames(video_path, frame_dir,
                           fps=fps, start_s=float(in_s), duration_s=dur)
        samples = [(f.src_pts_s - float(in_s), f.image_path) for f in fr]   # segment-local t
        n = len(samples); state = {"i": 0}

        def detect_fn(p):
            if progress:
                progress(state["i"], n); state["i"] += 1
            return det.detect(p)                                            # [(bbox, emb), ...]

        return subject_track(samples, detect_fn=detect_fn, person_boxes=person_boxes,
                             target_centroid=target_centroid)
