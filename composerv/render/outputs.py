"""Where outputs go: a persistent location organized by TYPE then DATE (never /tmp).

`<base>/<kind>/<YYYY-MM-DD>/<name>` — e.g. ~/Movies/composerV/mp4/2026-06-22/元旦.lite.mp4.
Base defaults to ~/Movies/composerV, overridable with the CV_OUT env var.
"""

from __future__ import annotations

import datetime
import os

DEFAULT_BASE = "~/Movies/composerV"


def output_base() -> str:
    return os.path.expanduser(os.environ.get("CV_OUT", DEFAULT_BASE))


def default_db() -> str:
    """The project's index/metadata DB, under the output base (~/Movies/composerV, or CV_OUT).
    Keeps scanned metadata in the project folder by default — never cwd or /tmp."""
    return os.path.join(output_base(), "composerv.db")


def default_music_dir() -> str:
    """The <feeling>/-tagged music library: ~/.composerv/music, overridable with CV_MUSIC_DIR."""
    return os.path.expanduser(os.environ.get("CV_MUSIC_DIR", "~/.composerv/music"))


def out_path(kind: str, name: str, *, base: str | None = None, date: str | None = None) -> str:
    """Path for an output of `kind` (mp4 / edl / fcpxml / storyboard) named `name`, under
    <base>/<kind>/<date>/. Creates the directory. date defaults to today."""
    base = base or output_base()
    date = date or datetime.date.today().isoformat()
    d = os.path.join(base, kind, date)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)
