"""Analyzer backend contract + registry.

A backend turns frame images into structured CaptionResults. Backends are pluggable and
selected by name from a registry (adding one touches a single place). Structured/
enum-constrained output (shot_type) is preferred to prose instructions to curb
confabulation; backends that can't do real structured output fall back to prompt+parse.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

# enum-constrained so the model picks from a fixed set, not free text
SHOT_TYPES = ["wide", "medium", "close", "extreme_close", "aerial", "pov", "unknown"]


class CaptionResult(BaseModel):
    caption: str = ""
    shot_type: str = "unknown"  # one of SHOT_TYPES
    objects: list[str] = Field(default_factory=list)
    ocr_text: str = ""
    salience: float = 0.0  # 0..1, the backend's sense of how notable the frame is


@runtime_checkable
class AnalyzerBackend(Protocol):
    name: str

    def caption_frames(self, image_paths: list[str]) -> list[CaptionResult]:
        """One CaptionResult per input image, in order."""
        ...


_BACKENDS: dict[str, Callable[[], AnalyzerBackend]] = {}


def register_backend(name: str, factory: Callable[[], AnalyzerBackend]) -> None:
    _BACKENDS[name] = factory


def get_backend(name: str) -> AnalyzerBackend:
    if name not in _BACKENDS:
        raise KeyError(f"unknown analyzer backend {name!r}; have {sorted(_BACKENDS)}")
    return _BACKENDS[name]()


def available_backends() -> list[str]:
    return sorted(_BACKENDS)
