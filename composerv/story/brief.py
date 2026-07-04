"""Build an Archive Brief from the indexed store. STUB (TDD red).

A compact, hierarchical summary an LLM can reason over to propose story angles, without
ever seeing raw video: a global header + a per-clip table aggregated from frame captions.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field

from composerv.store.db import Store


def _unique(seq):
    """Order-preserving unique."""
    seen = {}
    for x in seq:
        seen.setdefault(x, None)
    return list(seen)


class ClipBrief(BaseModel):
    asset_path: str
    name: str
    capture_time: str | None
    duration_s: float
    n_frames: int
    shot_types: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    key_caption: str = ""  # the highest-salience frame caption
    captions: list[str] = Field(default_factory=list)
    summary: str = ""  # clip-level video understanding (what happens over time)
    people: list[str] = Field(default_factory=list)  # named people present (the "who")


class ArchiveBrief(BaseModel):
    n_clips: int
    date_start: str | None
    date_end: str | None
    clips: list[ClipBrief] = Field(default_factory=list)

    def to_prompt_text(self) -> str:
        lines = [f"Archive: {self.n_clips} clip(s), {self.date_start or '?'} to {self.date_end or '?'}", ""]
        for c in self.clips:
            shots = "/".join(c.shot_types) or "?"
            objs = ", ".join(c.objects)
            lines.append(f"- {c.name} [{c.capture_time or '?'}, {c.duration_s:.0f}s, {shots}]:")
            if c.summary:
                lines.append(f"    what happens: {c.summary}")
            if c.people:
                lines.append(f"    people: {', '.join(c.people)}")
            if c.captions:
                lines.append(f"    moments: {' | '.join(c.captions)}")
            if objs:
                lines.append(f"    objects: {objs}")
        return "\n".join(lines)


def build_archive_brief(store: Store) -> ArchiveBrief:
    clips: list[ClipBrief] = []
    times: list[str] = []
    for asset in store.list_assets():
        if asset.kind != "video":
            continue
        caps = store.get_captions(asset.path)
        objects = _unique(o for c in caps for o in c.objects)
        shot_types = _unique(c.shot_type for c in caps)
        key = max(caps, key=lambda c: c.salience).caption if caps else ""
        clips.append(
            ClipBrief(
                asset_path=asset.path,
                name=os.path.basename(asset.path),
                capture_time=asset.capture_time,
                duration_s=asset.duration_s,
                n_frames=len(caps),
                shot_types=shot_types,
                objects=objects,
                key_caption=key,
                captions=[c.caption for c in caps],
                summary=store.get_clip_summary(asset.path),
                people=store.clip_person_names(asset.path, include_sensitive=False),
            )
        )
        if asset.capture_time:
            times.append(asset.capture_time)

    times.sort()
    return ArchiveBrief(
        n_clips=len(clips),
        date_start=times[0] if times else None,
        date_end=times[-1] if times else None,
        clips=clips,
    )
