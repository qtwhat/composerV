"""A deterministic fake analyzer backend, for testing the pipeline without a model."""

from __future__ import annotations

import os

from composerv.analyze.base import SHOT_TYPES, CaptionResult, register_backend


class FakeBackend:
    name = "fake"

    def caption_frames(self, image_paths: list[str]) -> list[CaptionResult]:
        return [
            CaptionResult(
                caption=f"frame {i}: {os.path.basename(p)}",
                shot_type=SHOT_TYPES[i % len(SHOT_TYPES)],
                objects=[],
                salience=(i % 10) / 10,
            )
            for i, p in enumerate(image_paths)
        ]


register_backend("fake", FakeBackend)
