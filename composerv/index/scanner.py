"""Walk a directory and probe primary media files. STUB (TDD red)."""

from __future__ import annotations

import os

from composerv.index.probe import PHOTO_EXTS, MediaInfo, probe_media

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".m2ts", ".3gp", ".webm"}


def scan_dir(root: str, include_photos: bool = False) -> list[MediaInfo]:
    """Probe every primary media file under root. Camera proxies (.LRF/.LRV) are skipped
    as primary assets (they're detected as a clip's camera_proxy instead)."""
    wanted = set(VIDEO_EXTS) | (PHOTO_EXTS if include_photos else set())
    out: list[MediaInfo] = []
    for dirpath, _dirs, names in os.walk(root):
        for name in sorted(names):
            if name.startswith("."):
                continue
            if os.path.splitext(name)[1].lower() not in wanted:
                continue
            try:
                out.append(probe_media(os.path.join(dirpath, name)))
            except Exception:
                continue  # unreadable / not real media: skip rather than fail the whole scan
    return out
