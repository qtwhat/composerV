"""Write a per-reel selection-rationale sidecar next to the rendered output (spec §13 item 3).

The shipped pipeline records the 'why this track' decision both in MontagePlan (in-memory) and
in this JSON sidecar beside the exported reel, so a viewer who dislikes the music can trace the
intent curve, the chosen track's curve, the per-axis match scores, and the beat-snap log.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from composerv.music.montage import MontagePlan


def write_rationale_sidecar(plan: "MontagePlan", reel_path: str) -> str:
    """Dump plan's rationale to <reel_path-stem>.music-rationale.json. Returns the path written."""
    from composerv.music.features import read_sidecar

    track_curve = None
    track_license = ""
    track_source = ""
    if plan.track:
        tf = read_sidecar(plan.track)
        if tf is not None:
            track_curve = tf.energy_curve
            track_license = tf.license or ""
            track_source = tf.source or ""
    data = {
        "reel": os.path.basename(reel_path),
        "feeling": plan.feeling,
        "label": plan.label,
        "track": plan.track,
        "tempo": plan.tempo,
        "match_score": plan.match_score,
        "match_breakdown": plan.match_breakdown,
        "library_gap": plan.library_gap,
        "beat_snaps": [list(s) for s in plan.beat_snaps],
        "intent": plan.intent.model_dump() if plan.intent is not None else None,
        "track_energy_curve": track_curve,
        "track_license": track_license,
        "track_source": track_source,
    }
    base, _ext = os.path.splitext(reel_path)
    out = base + ".music-rationale.json"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=2, default=str))
    return out
