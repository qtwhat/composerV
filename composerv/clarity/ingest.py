"""Ingest a folder of clips into the clarity store.

Per clip: make a uniform CFR proxy (reusing the camera .LRF when present) -> generate a
local 'what is this' summary -> extract display keyframes -> persist all to the store.
Idempotent: clips that already have a summary are skipped unless forced.

The summarize step is injectable so the orchestration is testable without the local model.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from composerv.clarity.keyframes import pick_keyframes
from composerv.clarity.summarize import ClaritySummary, summarize_clip
from composerv.index.probe import MediaInfo
from composerv.index.proxy import make_proxy
from composerv.index.scanner import scan_dir
from composerv.store.db import Store


def ingest_clip(
    media: MediaInfo,
    store: Store,
    *,
    proxy_path: str,
    frames_dir: str,
    summarize: Callable[[str, float], ClaritySummary] | None = None,
    keyframe_count: int = 4,
    video_toolbox: bool = True,
) -> ClaritySummary:
    """Make proxy + summary + keyframes for one clip and persist them. Returns the summary."""
    proxy = media.proxy_path
    if not proxy or not os.path.exists(proxy):
        if os.path.exists(proxy_path):
            proxy = proxy_path  # reuse a proxy built on an earlier run
        else:
            os.makedirs(os.path.dirname(proxy_path) or ".", exist_ok=True)
            proxy = make_proxy(media, proxy_path, use_videotoolbox=video_toolbox)
    store.upsert_asset(media, proxy_path=proxy)

    summarize = summarize or summarize_clip
    cs = summarize(proxy, media.duration_s)
    store.set_clarity_summary(media.path, cs.text, source=cs.source)
    # bridge: feed the same description to clip_summaries so the existing story layer
    # (brief -> storylines) can consume clarity output unchanged
    if cs.text:
        store.set_clip_summary(media.path, cs.text)

    kfs = pick_keyframes(proxy, frames_dir, media.duration_s, count=keyframe_count)
    store.set_keyframes(media.path, kfs)
    return cs


def ingest_photo(media: MediaInfo, store: Store, *, proxy_path: str, max_long_side: int = 1280) -> str:
    """Register a photo asset with a downscaled JPG proxy. The asset keeps the ORIGINAL pixel
    dimensions (reframe crops the original); the proxy is just a fast working copy for perception."""
    from PIL import Image

    os.makedirs(os.path.dirname(proxy_path) or ".", exist_ok=True)
    with Image.open(media.path) as im:
        im = im.convert("RGB")
        w, h = im.size
        scale = min(1.0, max_long_side / max(w, h)) if max(w, h) else 1.0
        if scale < 1.0:
            im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))))
        im.save(proxy_path, quality=90)
    if not media.width or not media.height:
        media.width, media.height = w, h   # record ORIGINAL dims for the crop math
    store.upsert_asset(media, proxy_path=proxy_path)
    return proxy_path


def ingest_photos(
    root: str,
    store: Store,
    *,
    work_dir: str,
    limit: int | None = None,
    force: bool = False,
    log: Callable[..., None] = print,
) -> int:
    """Register every photo under `root` (downscaled proxy + asset), oldest first. Idempotent:
    a photo already in the store with a proxy is skipped unless forced."""
    from composerv.index.scanner import scan_dir

    media = [m for m in scan_dir(root, include_photos=True) if m.kind == "photo"]
    media.sort(key=lambda m: (m.capture_time or "", m.path))
    if limit:
        media = media[:limit]
    proxy_dir = os.path.join(work_dir, "proxies")
    os.makedirs(proxy_dir, exist_ok=True)
    done = 0
    for m in media:
        name = os.path.basename(m.path)
        existing = store.get_asset(m.path)
        if not force and existing and existing.proxy_path and os.path.exists(existing.proxy_path):
            log(f"skip (have proxy): {name}")
            continue
        try:
            ingest_photo(m, store, proxy_path=os.path.join(proxy_dir, name + ".proxy.jpg"))
            done += 1
            log(f"[photo {done}] {name}")
        except Exception as e:  # one bad photo must not abort the run
            log(f"FAILED {name}: {e}")
    return done


def ingest_dir(
    root: str,
    store: Store,
    *,
    work_dir: str,
    summarize: Callable[[str, float], ClaritySummary] | None = None,
    limit: int | None = None,
    force: bool = False,
    keyframe_count: int = 4,
    video_toolbox: bool = True,
    log: Callable[..., None] = print,
) -> int:
    """Ingest every video under `root`, oldest first. Returns how many were processed."""
    media = [m for m in scan_dir(root) if m.kind == "video"]
    media.sort(key=lambda m: (m.capture_time or "", m.path))
    if limit:
        media = media[:limit]

    proxy_dir = os.path.join(work_dir, "proxies")
    os.makedirs(proxy_dir, exist_ok=True)
    done = 0
    for m in media:
        name = os.path.basename(m.path)
        if not force and store.get_clarity(m.path).summary:
            log(f"skip (have summary): {name}")
            continue
        proxy_path = os.path.join(proxy_dir, name + ".proxy.mp4")
        frames_dir = os.path.join(work_dir, "kf", name)
        try:
            cs = ingest_clip(m, store, proxy_path=proxy_path, frames_dir=frames_dir,
                             summarize=summarize, keyframe_count=keyframe_count,
                             video_toolbox=video_toolbox)
            done += 1
            log(f"[{done}] {name}: {cs.text[:80]}")
        except Exception as e:  # one bad clip must not abort the whole run
            log(f"FAILED {name}: {e}")
    return done
