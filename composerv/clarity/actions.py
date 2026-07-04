"""User actions over the clarity store: resolve a clip id, select/unselect, refine.

These are the operations the CLI (and later the web UI) call. Kept here, separate from the
CLI, so they are testable without spawning a process or the local/cloud model.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from composerv.clarity.summarize import ClaritySummary, claude_describe, summarize_clip
from composerv.store.db import Store


def resolve_clip(store: Store, token: str) -> str:
    """Map a token (full path or filename) to a stored asset path. Raises KeyError if it
    does not match exactly one clip."""
    if store.get_asset(token) is not None:
        return token
    matches = [m.path for m in store.list_assets() if os.path.basename(m.path) == token]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise KeyError(f"no clip matching {token!r}")
    raise KeyError(f"ambiguous id {token!r} matches {len(matches)} clips")


def set_selection(store: Store, tokens: list[str], selected: bool) -> list[str]:
    """Add/remove clips from the working set. Returns the resolved paths."""
    paths = [resolve_clip(store, t) for t in tokens]
    for p in paths:
        store.set_selected(p, selected)
    return paths


def _claude_refine(proxy: str, dur: float) -> ClaritySummary:
    return summarize_clip(proxy, dur, run=claude_describe, source="claude")


def refine_clip(
    store: Store,
    token: str,
    summarize: Callable[[str, float], ClaritySummary] | None = None,
) -> ClaritySummary:
    """Re-describe one clip with the cloud (Claude) for a sharper, more trustworthy summary.
    Only this clip's frames go to the cloud. Selection is preserved."""
    path = resolve_clip(store, token)
    asset = store.get_asset(path)
    proxy = (asset.proxy_path if asset else None) or path
    duration = asset.duration_s if asset else 0.0
    summarize = summarize or _claude_refine
    cs = summarize(proxy, duration)
    store.set_clarity_summary(path, cs.text, source=cs.source or "claude")
    return cs
