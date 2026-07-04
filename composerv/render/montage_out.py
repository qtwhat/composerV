"""Resolve a montage scope and write its outputs (preview EDL + FCPXML + storyboard).

The glue behind `composerv montage`: kept out of the CLI so it is unit-testable without
ffmpeg/librosa. The EDL references PROXIES (uniform CFR, for the live preview); the FCPXML
references the ORIGINAL source files (for the FCP finish)."""

from __future__ import annotations

import json
import os

from composerv.render.fcpxml.emitter import intention_to_fcpxml
from composerv.render.preview.edl import intention_to_edl
from composerv.render.storyboard import build_edit_shots, render_edit_storyboard


def resolve_scope(store, scope: str) -> list[str]:
    """`selected` -> the working set; `all` -> every clip with a proxy; anything else is a
    capture-time prefix (e.g. a date `2026-01-01`)."""
    scope = (scope or "").strip()
    if scope == "selected":
        return list(store.list_selected())
    if scope == "all":
        return [a.path for a in store.list_assets() if a.proxy_path]
    return [a.path for a in store.list_assets()
            if a.capture_time and a.capture_time.startswith(scope)]


def write_montage_outputs(plan, store, out: str, event: str = "", *,
                          base: str | None = None, date: str | None = None) -> dict[str, str]:
    """Write the reel's EDL (proxies), FCPXML (originals) and storyboard. Returns {kind: path}.

    If `out` is a path (contains a separator) it's used as a literal prefix (legacy). Otherwise
    `out` is a NAME and each file is routed to the organized output tree
    <base>/<kind>/<date>/<name>.<ext> (base = ~/Movies/composerV or $CV_OUT)."""
    il = plan.intention
    sources: list[str] = []
    for s in il.segments:
        if s.kind in ("clip", "photo") and s.source_id and s.source_id not in sources:
            sources.append(s.source_id)
    from composerv.render.preview.composition import oriented_size
    proxies, originals = {}, {}
    for sid in sources:
        a = store.get_asset(sid)
        # Landscape (16:9) clips ride the CFR proxy (uniform, cheap to preview). Aspect-mismatched
        # (vertical/rotated) clips use the ORIGINAL so reframe can crop it to fill the canvas; a
        # 16:9 proxy of a vertical source is already pillarboxed and can't be recovered.
        if a and a.proxy_path:
            w, h = oriented_size(a.path) if a.path else (16, 9)
            proxies[sid] = a.path if (h and abs(w / h - 16 / 9) > 0.02) else a.proxy_path
        else:
            proxies[sid] = sid  # fall back to original
        originals[sid] = sid

    name = os.path.basename(out) or "montage"
    if os.sep in out:  # explicit path prefix -> write together (legacy)
        paths = {"edl": out + ".edl.json", "fcpxml": out + ".fcpxml",
                 "storyboard": out + "_storyboard.html"}
    else:              # route by type + date under the output base
        from composerv.render.outputs import out_path
        paths = {"edl": out_path("edl", name + ".edl.json", base=base, date=date),
                 "fcpxml": out_path("fcpxml", name + ".fcpxml", base=base, date=date),
                 "storyboard": out_path("storyboard", name + "_storyboard.html", base=base, date=date)}
    with open(paths["edl"], "w") as f:
        json.dump(intention_to_edl(il, proxies, title=event), f, indent=2)
    with open(paths["fcpxml"], "w") as f:
        f.write(intention_to_fcpxml(il, originals, project_name=name))
    with open(paths["storyboard"], "w") as f:
        f.write(render_edit_storyboard(build_edit_shots(il, store), title=name, event=event))

    # Write music-rationale sidecar next to the EDL (the primary reel descriptor).
    # Strip the ".edl.json" suffix so the sidecar shares the clean stem with any future mp4.
    edl_path = paths["edl"]
    reel_base = edl_path[: -len(".edl.json")] if edl_path.endswith(".edl.json") else os.path.splitext(edl_path)[0]
    reel_ref = reel_base + ".mp4"   # nominal reel path; the mp4 need not exist yet
    try:
        from composerv.music.rationale import write_rationale_sidecar
        sidecar = write_rationale_sidecar(plan, reel_ref)
        paths["rationale"] = sidecar
    except Exception:
        pass  # sidecar is informational; never abort an export for it

    return paths
