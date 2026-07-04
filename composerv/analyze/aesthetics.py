"""On-device aesthetics scoring (Apple Vision) + distillation to director-readable tags.

The score comes from a tiny Swift CLI (swift/aesthetics.swift) shelled out like ffmpeg; the
pure distillation/selection helpers live here and are unit-tested without the binary. A `curve`
(and `series`) is a time-ordered list of (t_seconds, overall_score, is_utility) samples.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

_SHARP = "[清晰·构图好]"
_WEAK = "[弱/过渡]"


def distill_quality(score: float | None, is_utility: bool) -> str:
    """Raw aesthetic score (-1..1) + Vision's isUtility flag -> a short director-facing tag.
    Only the notable ends get a tag; the unremarkable middle returns '' (no prompt noise)."""
    if score is None:
        return ""
    if is_utility or score <= -0.2:
        return _WEAK
    if score >= 0.4:
        return _SHARP
    return ""


def best_moment(series, duration_s: float):
    """Timestamp of the highest-scoring frame = a clip's best instant. Excludes the first/last
    0.3s (clamped openings/tails); requires a non-negative score, else None."""
    if not series:
        return None
    hi = max(0.3, duration_s - 0.3)
    inner = [(t, s) for (t, s, _u) in series if 0.3 <= t <= hi]
    if not inner:
        inner = [(t, s) for (t, s, _u) in series]
    t_best, s_best = max(inner, key=lambda ts: ts[1])
    return t_best if s_best >= 0.0 else None


def quality_tag_at(t: float, curve) -> str:
    """Tag for a moment at time t: distil the nearest curve sample. '' if the curve is empty."""
    if not curve:
        return ""
    _ts, score, util = min(curve, key=lambda c: abs(c[0] - t))
    return distill_quality(score, bool(util))


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _binary_path() -> str:
    """The built Swift scorer: CV_AESTHETICS_BIN, else the cwd-relative build (back-compat),
    else a repo-rooted path — so scoring works no matter which directory you run from."""
    env = os.environ.get("CV_AESTHETICS_BIN")
    if env:
        return os.path.expanduser(env)
    cwd_rel = os.path.join(".composerv", "bin", "aesthetics")
    if os.path.exists(cwd_rel):
        return cwd_rel
    return os.path.join(_REPO_ROOT, ".composerv", "bin", "aesthetics")


def _ensure_binary() -> str | None:
    """Path to the built Swift scorer, compiling it on first use. None if swiftc/build unavailable."""
    out = _binary_path()
    if os.path.exists(out):
        return out
    src = os.path.join(_REPO_ROOT, "swift", "aesthetics.swift")
    if not os.path.exists(src):
        src = os.path.join("swift", "aesthetics.swift")  # fallback: running from a checkout
    if not os.path.exists(src):
        print(f"[aesthetics] swift source missing: {src}", file=sys.stderr)
        return None
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        subprocess.run(["swiftc", "-O", "-parse-as-library", src, "-o", out], check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(f"[aesthetics] build failed (need Xcode command line tools): {e!r}", file=sys.stderr)
        return None
    return out


def score_frames(image_paths, binary_path: str | None = None):
    """Score each frame on-device via the Swift CLI -> {path: (score, is_utility)}. Returns {}
    (graceful no-op) if the binary is unavailable or the call fails; never raises."""
    if not image_paths:
        return {}
    binary = binary_path or _ensure_binary()
    if not binary:
        return {}
    try:
        proc = subprocess.run([binary, *image_paths], check=True, capture_output=True, text=True)
        rows = json.loads(proc.stdout or "[]")
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError, OSError) as e:
        print(f"[aesthetics] scoring failed: {e!r}", file=sys.stderr)
        return {}
    out = {}
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and "path" in r and "score" in r:
            try:
                out[str(r["path"])] = (float(r["score"]), bool(r.get("isUtility", False)))
            except (TypeError, ValueError):
                continue
    return out


def analyze_aesthetics(proxy_path, duration_s, *, aes_fps: float = 2.0, score_fn=None,
                       frames_dir: str | None = None):
    """Sample frames at aes_fps, score them on-device, return (best_t, curve). (None, []) when
    there is nothing to score (no proxy / zero duration / scorer unavailable)."""
    from composerv.index.frames import sample_frames

    if not proxy_path or not os.path.exists(proxy_path) or duration_s <= 0:
        return (None, [])
    with tempfile.TemporaryDirectory(prefix="cv_aes_") as td:
        work = frames_dir or td
        pairs = [(f.src_pts_s, f.image_path) for f in sample_frames(proxy_path, work, fps=aes_fps)]
        scores = (score_fn or score_frames)([p for _t, p in pairs])
        curve = sorted((t, *scores[p]) for (t, p) in pairs if p in scores)
        return (best_moment(curve, duration_s), curve)
