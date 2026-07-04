# Aesthetics perception (v1) implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an on-device (Apple Vision) aesthetics scoring pass that gives the director a per-moment quality tag and a per-clip best moment, with the raw score curve kept in the store for the executor.

**Architecture:** Serial, director-core. A tiny Swift CLI scores frames; pure Python distils scores into director-readable tags. Aesthetics lives in its own `clip_aesthetics` table (best_t + curve); the per-moment tag is derived from the curve at footage-table assembly. `clip_moments` / `ClipMoment` are untouched. v1 needs no new director output fields (the director opens at the best moment via the existing `in_s` and culls by omission).

**Tech Stack:** Python 3, SQLite (via `composerv.store.db`), Swift + Apple Vision (`CalculateImageAestheticsScoresRequest`, macOS 15+), ffmpeg frame sampling (`composerv.index.frames.sample_frames`), pytest.

## Global Constraints

- Platform: macOS 15+ for Vision aesthetics (this machine is macOS 26, satisfied); on-device only, no cloud.
- Soft signal under human-led: a `[弱/过渡]` tag is advisory; the director's rule 1 (human-led) always wins. No automatic hard culling.
- Frame grid default 2 fps, tunable via `--aes-fps`.
- v1 adds NO new director output fields (decisions ride the existing `in_s` + omission).
- Graceful degradation: a missing/failed Swift binary yields no tags and `best_t=None`; the pipeline continues. Never fail silently — log to stderr.
- Scoring runs on the 1280×720 proxy (blur is underestimated; accepted for v1).
- Out of scope (v2): crop-judge, saliency pass, growing the director output contract with crop instructions, machine-side uses of the curve.

---

### Task 1: Pure distillation + selection functions

**Files:**
- Create: `composerv/analyze/aesthetics.py`
- Test: `tests/test_aesthetics.py`

**Interfaces:**
- Produces: `distill_quality(score: float | None, is_utility: bool) -> str`; `best_moment(series, duration_s: float) -> float | None` where `series` is `list[tuple[float, float, bool]]` of `(t, score, is_utility)`; `quality_tag_at(t: float, curve) -> str` where `curve` has the same shape as `series`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_aesthetics.py
from composerv.analyze.aesthetics import best_moment, distill_quality, quality_tag_at


def test_distill_quality_tags_only_the_notable_ends():
    assert distill_quality(0.6, False) == "[清晰·构图好]"
    assert distill_quality(-0.4, False) == "[弱/过渡]"
    assert distill_quality(0.1, True) == "[弱/过渡]"   # isUtility flags filler even at an okay score
    assert distill_quality(0.1, False) == ""           # unremarkable middle: no tag (no prompt noise)
    assert distill_quality(None, False) == ""          # no score -> no tag


def test_best_moment_picks_top_score_excluding_head_and_tail():
    series = [(0.0, 0.9, False), (1.0, 0.3, False), (2.0, 0.7, False), (4.0, 0.95, False)]
    # duration 4.2 -> head/tail 0.3 windows drop t=0.0 and t=4.0; best of the inner is t=2.0
    assert best_moment(series, 4.2) == 2.0


def test_best_moment_returns_none_when_all_weak():
    assert best_moment([(1.0, -0.3, False), (2.0, -0.1, True)], 3.0) is None
    assert best_moment([], 3.0) is None


def test_quality_tag_at_uses_nearest_curve_sample():
    curve = [(0.0, -0.5, False), (2.0, 0.8, False)]
    assert quality_tag_at(0.1, curve) == "[弱/过渡]"   # nearest is t=0.0
    assert quality_tag_at(1.9, curve) == "[清晰·构图好]"  # nearest is t=2.0
    assert quality_tag_at(1.0, []) == ""               # empty curve -> no tag
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_aesthetics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'composerv.analyze.aesthetics'`

- [ ] **Step 3: Write the minimal implementation**

```python
# composerv/analyze/aesthetics.py
"""On-device aesthetics scoring (Apple Vision) + distillation to director-readable tags.

The score comes from a tiny Swift CLI (swift/aesthetics.swift) shelled out like ffmpeg; the
pure distillation/selection helpers live here and are unit-tested without the binary. A `curve`
(and `series`) is a time-ordered list of (t_seconds, overall_score, is_utility) samples.
"""

from __future__ import annotations

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_aesthetics.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add composerv/analyze/aesthetics.py tests/test_aesthetics.py
git commit -m "feat: pure aesthetics distillation (tag, best-moment, curve lookup)"
```

---

### Task 2: Swift scorer CLI + `score_frames` subprocess wrapper

**Files:**
- Create: `swift/aesthetics.swift`
- Modify: `composerv/analyze/aesthetics.py` (add `_binary_path`, `_ensure_binary`, `score_frames`)
- Test: `tests/test_aesthetics.py` (add subprocess tests)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `score_frames(image_paths: list[str], binary_path: str | None = None) -> dict[str, tuple[float, bool]]` mapping each input path to `(score, is_utility)`; missing paths are simply absent. Returns `{}` on any failure.

- [ ] **Step 1: Write the failing test (deterministic, fake binary)**

```python
# tests/test_aesthetics.py  (append)
from composerv.analyze.aesthetics import score_frames


def test_score_frames_parses_binary_json(tmp_path):
    fake = tmp_path / "fake_aes"
    fake.write_text(
        '#!/bin/sh\n'
        'printf \'[{"path":"a.jpg","score":0.7,"isUtility":false},'
        '{"path":"b.jpg","score":-0.5,"isUtility":true}]\'\n'
    )
    fake.chmod(0o755)
    out = score_frames(["a.jpg", "b.jpg"], binary_path=str(fake))
    assert out == {"a.jpg": (0.7, False), "b.jpg": (-0.5, True)}


def test_score_frames_graceful_when_binary_missing(tmp_path):
    assert score_frames(["a.jpg"], binary_path=str(tmp_path / "nope")) == {}


def test_score_frames_empty_input_is_noop():
    assert score_frames([]) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_aesthetics.py::test_score_frames_parses_binary_json -v`
Expected: FAIL with `ImportError: cannot import name 'score_frames'`

- [ ] **Step 3: Write the Swift CLI**

```swift
// swift/aesthetics.swift
// Score each image path's aesthetic quality on-device with Apple Vision, emit JSON to stdout:
//   [{"path": "...", "score": <Double -1..1>, "isUtility": <Bool>}, ...]
// Build:  swiftc -O swift/aesthetics.swift -o .composerv/bin/aesthetics
import Foundation
import Vision
import ImageIO

@main
struct Aesthetics {
    static func main() async {
        let paths = Array(CommandLine.arguments.dropFirst())
        var out: [[String: Any]] = []
        for path in paths {
            guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
                  let cg = CGImageSourceCreateImageAtIndex(src, 0, nil) else { continue }
            if #available(macOS 15.0, *) {
                let request = CalculateImageAestheticsScoresRequest()
                do {
                    let obs = try await request.perform(on: cg)
                    out.append(["path": path,
                                "score": Double(obs.overallScore),
                                "isUtility": obs.isUtility])
                } catch {
                    FileHandle.standardError.write(Data("aesthetics err \(path): \(error)\n".utf8))
                }
            }
        }
        if let data = try? JSONSerialization.data(withJSONObject: out) {
            FileHandle.standardOutput.write(data)
        }
    }
}
```

- [ ] **Step 4: Add the Python wrapper**

```python
# composerv/analyze/aesthetics.py  (append; add stdlib imports at top: json, os, subprocess, sys)
import json
import os
import subprocess
import sys


def _binary_path() -> str:
    return os.path.join(".composerv", "bin", "aesthetics")


def _ensure_binary() -> str | None:
    """Path to the built Swift scorer, compiling it on first use. None if swiftc/build unavailable."""
    out = _binary_path()
    if os.path.exists(out):
        return out
    src = os.path.join("swift", "aesthetics.swift")
    if not os.path.exists(src):
        print(f"[aesthetics] swift source missing: {src}", file=sys.stderr)
        return None
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        subprocess.run(["swiftc", "-O", src, "-o", out], check=True, capture_output=True)
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
```

- [ ] **Step 5: Run the subprocess tests to verify they pass**

Run: `uv run pytest tests/test_aesthetics.py -v`
Expected: PASS (all tests, including the 3 new subprocess tests)

- [ ] **Step 6: Verify the real Swift binary builds and scores (manual, on macOS 15+)**

Run:
```bash
mkdir -p .composerv/bin && swiftc -O swift/aesthetics.swift -o .composerv/bin/aesthetics && \
ffmpeg -y -loglevel error -f lavfi -i color=c=gray:s=320x240 -frames:v 1 /tmp/aes_probe.jpg && \
.composerv/bin/aesthetics /tmp/aes_probe.jpg
```
Expected: a JSON array like `[{"path":"/tmp/aes_probe.jpg","score":<number>,"isUtility":<bool>}]`. If `swiftc` is missing, install Xcode command line tools (`xcode-select --install`); the pipeline still degrades gracefully without it.

- [ ] **Step 7: Commit**

```bash
git add swift/aesthetics.swift composerv/analyze/aesthetics.py tests/test_aesthetics.py
git commit -m "feat: swift Vision aesthetics scorer + score_frames subprocess wrapper"
```

---

### Task 3: `clip_aesthetics` store table

**Files:**
- Modify: `composerv/store/db.py` (add `ClipAesthetics` model, the table in `_init_schema`, `set_clip_aesthetics`, `get_clip_aesthetics`)
- Test: `tests/test_store.py` (append)

**Interfaces:**
- Produces: `Store.set_clip_aesthetics(asset_path: str, best_t: float | None, curve: list[tuple[float, float, bool]]) -> None`; `Store.get_clip_aesthetics(asset_path: str) -> ClipAesthetics | None` where `ClipAesthetics` has `.best_t: float | None` and `.curve: list[tuple[float, float, bool]]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store.py  (append)
def test_clip_aesthetics_roundtrip_replace_and_default(tmp_path):
    from composerv.store.db import Store
    from composerv.index.probe import MediaInfo

    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video"))
    assert s.get_clip_aesthetics("/m/a.mp4") is None           # none yet
    s.set_clip_aesthetics("/m/a.mp4", 2.0, [(0.0, -0.3, True), (2.0, 0.8, False)])
    got = s.get_clip_aesthetics("/m/a.mp4")
    assert got.best_t == 2.0
    assert got.curve == [(0.0, -0.3, True), (2.0, 0.8, False)]
    s.set_clip_aesthetics("/m/a.mp4", None, [(1.0, 0.1, False)])  # replace, never append
    got = s.get_clip_aesthetics("/m/a.mp4")
    assert got.best_t is None and got.curve == [(1.0, 0.1, False)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store.py::test_clip_aesthetics_roundtrip_replace_and_default -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'set_clip_aesthetics'`

- [ ] **Step 3: Add the model, table, and methods**

In `composerv/store/db.py`, add the model near the other `BaseModel`s (after `ClarityRecord`):

```python
class ClipAesthetics(BaseModel):
    """Per-clip aesthetics: the best instant + the full score curve (raw, for the executor)."""
    best_t: float | None = None
    curve: list[tuple[float, float, bool]] = []   # (t_seconds, overall_score, is_utility)
```

In `_init_schema`, add this table to the `executescript` block (next to `clip_moments`):

```sql
            CREATE TABLE IF NOT EXISTS clip_aesthetics (
                asset_path TEXT PRIMARY KEY,
                best_t REAL,
                curve TEXT DEFAULT ''
            );
```

Add the two methods (next to `get_clip_moments_rich`):

```python
    def set_clip_aesthetics(self, asset_path: str, best_t: float | None, curve) -> None:
        """Replace a clip's aesthetics (best instant + raw score curve)."""
        payload = json.dumps([[float(t), float(s), bool(u)] for t, s, u in curve])
        self.conn.execute(
            """INSERT INTO clip_aesthetics (asset_path, best_t, curve) VALUES (?,?,?)
               ON CONFLICT(asset_path) DO UPDATE SET best_t=excluded.best_t, curve=excluded.curve""",
            (asset_path, best_t, payload),
        )
        self.conn.commit()

    def get_clip_aesthetics(self, asset_path: str):
        """-> ClipAesthetics, or None if this clip has no aesthetics cached yet."""
        row = self.conn.execute(
            "SELECT best_t, curve FROM clip_aesthetics WHERE asset_path = ?", (asset_path,)
        ).fetchone()
        if row is None:
            return None
        try:
            raw = json.loads(row["curve"] or "[]")
        except (json.JSONDecodeError, TypeError):
            raw = []
        curve = [(float(c[0]), float(c[1]), bool(c[2]))
                 for c in raw if isinstance(c, (list, tuple)) and len(c) == 3]
        return ClipAesthetics(best_t=row["best_t"], curve=curve)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_store.py::test_clip_aesthetics_roundtrip_replace_and_default -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add composerv/store/db.py tests/test_store.py
git commit -m "feat: clip_aesthetics store table (best_t + raw score curve)"
```

---

### Task 4: `analyze_aesthetics` + wire into `analyze_clip`

**Files:**
- Modify: `composerv/analyze/aesthetics.py` (add `analyze_aesthetics`)
- Modify: `composerv/clarity/analyze.py` (call it from `analyze_clip`; thread `aes_fps`/`enable_aesthetics`)
- Test: `tests/test_analyze.py` (append)

**Interfaces:**
- Consumes: `score_frames` (Task 2), `best_moment` (Task 1), `Store.set_clip_aesthetics` (Task 3).
- Produces: `analyze_aesthetics(proxy_path: str, duration_s: float, *, aes_fps: float = 2.0, score_fn=None, frames_dir: str | None = None) -> tuple[float | None, list]` returning `(best_t, curve)`. `analyze_clip(..., aesthetics_fn=None, aes_fps=2.0, enable_aesthetics=True)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analyze.py  (append; imports at top of file as needed)
def test_analyze_clip_stores_aesthetics_from_injected_scores(tmp_path):
    from composerv.clarity.analyze import analyze_clip
    from composerv.store.db import Store
    from composerv.index.probe import MediaInfo

    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=10.0), proxy_path="/p/a.mp4")

    def fake_aes(proxy, dur, *, aes_fps=2.0):
        assert dur == 10.0
        return (3.0, [(0.0, -0.4, True), (3.0, 0.9, False)])

    analyze_clip(s, "/m/a.mp4",
                 visual_fn=lambda p, d: [(0.0, "x"), (3.0, "y")],
                 speech_fn=lambda p: [],
                 aesthetics_fn=fake_aes)
    got = s.get_clip_aesthetics("/m/a.mp4")
    assert got is not None and got.best_t == 3.0
    assert got.curve == [(0.0, -0.4, True), (3.0, 0.9, False)]


def test_analyze_clip_skips_aesthetics_when_disabled(tmp_path):
    from composerv.clarity.analyze import analyze_clip
    from composerv.store.db import Store
    from composerv.index.probe import MediaInfo

    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=10.0), proxy_path="/p/a.mp4")
    analyze_clip(s, "/m/a.mp4", visual_fn=lambda p, d: [(0.0, "x")], speech_fn=lambda p: [],
                 enable_aesthetics=False)
    assert s.get_clip_aesthetics("/m/a.mp4") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_analyze.py -k aesthetics -v`
Expected: FAIL with `TypeError: analyze_clip() got an unexpected keyword argument 'aesthetics_fn'`

- [ ] **Step 3: Add `analyze_aesthetics`**

```python
# composerv/analyze/aesthetics.py  (append; add `import tempfile` near the other imports)
import tempfile


def analyze_aesthetics(proxy_path, duration_s, *, aes_fps: float = 2.0, score_fn=None,
                       frames_dir: str | None = None):
    """Sample frames at aes_fps, score them on-device, return (best_t, curve). (None, []) when
    there is nothing to score (no proxy / zero duration / scorer unavailable)."""
    from composerv.index.frames import sample_frames

    if not proxy_path or not os.path.exists(proxy_path) or duration_s <= 0:
        return (None, [])
    frames_dir = frames_dir or tempfile.mkdtemp(prefix="cv_aes_")
    pairs = [(f.src_pts_s, f.image_path) for f in sample_frames(proxy_path, frames_dir, fps=aes_fps)]
    scores = (score_fn or score_frames)([p for _t, p in pairs])
    curve = sorted((t, *scores[p]) for (t, p) in pairs if p in scores)
    return (best_moment(curve, duration_s), curve)
```

- [ ] **Step 4: Wire into `analyze_clip`**

In `composerv/clarity/analyze.py`, change the `analyze_clip` signature and add the aesthetics step. Replace the current body from the signature through the video branch:

```python
def analyze_clip(
    store,
    path: str,
    *,
    visual_fn: Callable[[str, float], list] | None = None,
    speech_fn: Callable[[str], list] | None = None,
    aesthetics_fn: Callable | None = None,
    aes_fps: float = 2.0,
    enable_aesthetics: bool = True,
) -> tuple[int, int]:
    """Run perception for one clip and cache it in the store (clip_moments + transcript +
    clip_aesthetics). Returns (n_moments, n_sentences). A photo gets the single-image visual pass
    and NO transcript/aesthetics. visual_fn/speech_fn/aesthetics_fn override the live models."""
    a = store.get_asset(path)
    if not a:
        return (0, 0)
    proxy = a.proxy_path or a.path or ""
    if a.kind == "photo":
        vis = (visual_fn or _default_photo_visual)(proxy, a.duration_s) or []
        store.set_clip_moments(path, vis)
        return (len(vis), 0)  # a still has no audio

    vis = (visual_fn or _default_visual)(proxy, a.duration_s) or []
    store.set_clip_moments(path, vis)  # items may be (t,text) or (t,text,ocr[,objects])

    if enable_aesthetics:
        from composerv.analyze.aesthetics import analyze_aesthetics
        best_t, curve = (aesthetics_fn or analyze_aesthetics)(proxy, a.duration_s, aes_fps=aes_fps)
        if curve:
            store.set_clip_aesthetics(path, best_t, curve)

    from composerv.music.montage import _default_speech

    sp = (speech_fn or _default_speech)(proxy) or []
    store.set_transcript(path, [(float(w[0]), float(w[1]), w[2] if len(w) > 2 else "") for w in sp])
    return (len(vis), len(sp))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_analyze.py -v`
Expected: PASS (existing analyze tests + the 2 new aesthetics tests)

- [ ] **Step 6: Commit**

```bash
git add composerv/analyze/aesthetics.py composerv/clarity/analyze.py tests/test_analyze.py
git commit -m "feat: analyze_clip computes + caches per-clip aesthetics (best_t + curve)"
```

---

### Task 5: footage table renders the tag + best-moment header

**Files:**
- Modify: `composerv/director/table.py` (`build_footage_table`)
- Test: `tests/test_director.py` (append)

**Interfaces:**
- Consumes: per-clip dict may carry `best_t: float | None` and per-visual tuple may carry a quality tag at index 4: `(t, text, ocr, objects, qtag)`.
- Produces: the rendered table string, unchanged contract otherwise.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_director.py  (append)
def test_table_renders_quality_tag_and_best_header():
    table = build_footage_table([{
        "clip_id": "c", "people": [], "duration": 10.0, "best_t": 3.2,
        "visual": [(0.0, "走进来", "", [], "[弱/过渡]"), (3.2, "特写", "", [], "[清晰·构图好]")],
        "speech": [],
    }])
    assert "best ~3.2s" in table                    # per-clip best moment in the header
    assert "走进来  [弱/过渡]" in table              # tag appended to its visual row
    assert "特写  [清晰·构图好]" in table


def test_table_without_aesthetics_is_unchanged():
    table = build_footage_table([{
        "clip_id": "c", "people": [], "duration": 10.0,
        "visual": [(0.0, "走进来")], "speech": [],
    }])
    assert "走进来" in table and "best ~" not in table   # no aesthetics -> no header, no tag
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_director.py::test_table_renders_quality_tag_and_best_header -v`
Expected: FAIL (`best ~3.2s` not in table)

- [ ] **Step 3: Implement the rendering**

In `composerv/director/table.py`, inside `build_footage_table`'s per-clip loop, add the best-moment header after the `note` block:

```python
        if c.get("note"):
            head += f"   note: {c['note']}"
        best_t = c.get("best_t")
        if best_t is not None:
            head += f"   best ~{float(best_t):.1f}s"
```

And change the visual-row loop to read the quality tag at index 4:

```python
        for v in c.get("visual", []):
            t, txt = float(v[0]), v[1]
            qtag = v[4] if len(v) > 4 else ""           # v[3]=objects stays ignored (boxes -> reframe)
            rows.append(("visual", t, t, f"{txt}  {qtag}" if qtag else txt))
            ocr = v[2] if len(v) > 2 else ""
            if ocr:
                rows.append(("text", t, t, f"on screen: {ocr}"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_director.py -k "table" -v`
Expected: PASS (the 2 new tests plus the existing table tests, including `test_table_tolerates_objects_in_visual_tuple`)

- [ ] **Step 5: Commit**

```bash
git add composerv/director/table.py tests/test_director.py
git commit -m "feat: footage table renders quality tag + best-moment header"
```

---

### Task 6: montage assembly derives the tag + passes best_t

**Files:**
- Modify: `composerv/director/montage.py:81-95`
- Test: `tests/test_director.py` (append)

**Interfaces:**
- Consumes: `Store.get_clip_aesthetics` (Task 3), `quality_tag_at` (Task 1), the table fields (Task 5).
- Produces: the footage table the director reads now carries quality tags + best moments for store-cached clips.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_director.py  (append)
def test_build_director_montage_surfaces_aesthetics(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=20.0,
                             capture_time="2026-01-01T15:00:00"), proxy_path="/p/a.mp4")
    s.set_clip_summary("/m/a.mp4", "a")
    s.set_clip_moments("/m/a.mp4", [(0.0, "走过牌坊"), (3.0, "孩子特写")])
    s.set_clip_aesthetics("/m/a.mp4", 3.0, [(0.0, -0.5, False), (3.0, 0.9, False)])
    d = tmp_path / "music" / "calm"; d.mkdir(parents=True); (d / "t.mp3").write_bytes(b"x")

    captured = {}

    def director_fn(prompt):
        captured["p"] = prompt
        return '{"segments":[{"clip_id":"a","in_s":3.0,"out_s":6.0,"kind":"moment","duck_music":false}]}'

    build_director_montage(s, ["/m/a.mp4"], music_dir=str(tmp_path / "music"), feeling="calm",
                           director_fn=director_fn, beat_fn=lambda t: (120.0, []))
    assert "best ~3.0s" in captured["p"]              # best moment reached the director
    assert "孩子特写  [清晰·构图好]" in captured["p"]   # nearest-sample tag on the strong moment
    assert "走过牌坊  [弱/过渡]" in captured["p"]        # and on the weak one
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_director.py::test_build_director_montage_surfaces_aesthetics -v`
Expected: FAIL (`best ~3.0s` not in prompt)

- [ ] **Step 3: Implement the wire-up**

In `composerv/director/montage.py`, replace the store-read branch (lines 81-84) and add `best_t` to the row dict (line 87-95):

```python
        if visual_fn:
            visual = visual_fn(proxy, a.duration_s) or []
            best_t = None
        else:
            from composerv.analyze.aesthetics import quality_tag_at
            aes = store.get_clip_aesthetics(p)
            curve = aes.curve if aes else []
            best_t = aes.best_t if aes else None
            # boxes stay in the store (reframe); the table ignores v[3]. v[4] = derived quality tag.
            visual = [(m.t, m.text, m.ocr, m.objects, quality_tag_at(m.t, curve))
                      for m in store.get_clip_moments_rich(p)]
        raw_speech = (vad_fn(proxy) if vad_fn else store.get_transcript(p)) or []
        speech = [(float(w[0]), float(w[1]), w[2] if len(w) > 2 else "") for w in raw_speech]
        rows.append({
            "clip_id": sid,
            "people": store.clip_person_names(p),
            "note": "",
            "duration": a.duration_s,
            "photo": a.kind == "photo",
            "visual": visual,
            "best_t": best_t,
            "speech": speech,
        })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_director.py -v`
Expected: PASS (the new test + all existing director tests)

- [ ] **Step 5: Commit**

```bash
git add composerv/director/montage.py tests/test_director.py
git commit -m "feat: montage derives per-moment quality tags + best_t from the store"
```

---

### Task 7: director prompt rules for best-moment in-point + soft cull

**Files:**
- Modify: `composerv/director/prompt.py` (`_RULES`)
- Test: `tests/test_director.py` (append)

**Interfaces:**
- Consumes: nothing new (text-only change).
- Produces: the director prompt explains the quality tag and best moment, and how to act on them under the human-led rule.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_director.py  (append)
def test_prompt_explains_quality_and_best_moment():
    p = build_director_prompt("[clip a] ...", feeling="calm", budget_s=300)
    assert "best ~" in p                       # the header hint is documented
    assert "[弱/过渡]" in p and "[清晰·构图好]" in p   # both tags are explained
    # the cull stays soft: human-led still wins
    assert "human-led" in p.lower() or "Human-led" in p
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_director.py::test_prompt_explains_quality_and_best_moment -v`
Expected: FAIL (`best ~` not in prompt)

- [ ] **Step 3: Extend the rules**

In `composerv/director/prompt.py`, change rule 8 in `_RULES` and append a quality clause to rule 2. Replace the rule-8 line:

```
8. Open a visual shot on its active / meaningful instant, not always the clip's start.
```

with:

```
8. Open a visual shot on its active / meaningful instant, not always the clip's start. Some clips
   show a quality hint: a header "best ~Xs" (the sharpest, best-composed instant) and per-row tags
   "[清晰·构图好]" (a strong frame) or "[弱/过渡]" (soft / transitional / filler). Prefer opening a
   shot at or near its best moment, and prefer a "[清晰·构图好]" frame over a "[弱/过渡]" one.
```

And append to rule 2 (after "repetitive nothing."):

```
   A "[弱/过渡]" tag is a hint that a frame is weak (blurry/transitional) — prefer to drop it or not
   open on it. This is only a hint: rule 1 (human-led) always wins, so keep a weak-looking frame if a
   human marked it important.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_director.py -k prompt -v`
Expected: PASS (new test + existing prompt tests)

- [ ] **Step 5: Commit**

```bash
git add composerv/director/prompt.py tests/test_director.py
git commit -m "feat: director prompt uses best-moment + quality tags (soft, human-led wins)"
```

---

### Task 8: CLI flags `--no-aesthetics` / `--aes-fps`

**Files:**
- Modify: `composerv/clarity/analyze.py` (`analyze_scope` threads the two params)
- Modify: `composerv/cli/main.py:153-171` (the `analyze` command)
- Test: `tests/test_analyze.py` (append)

**Interfaces:**
- Consumes: `analyze_clip(..., aes_fps, enable_aesthetics)` (Task 4).
- Produces: `analyze_scope(store, paths, *, visual_fn=None, speech_fn=None, cooldown_s=0.0, on_progress=None, aes_fps=2.0, enable_aesthetics=True)`; CLI options `--aes-fps` and `--no-aesthetics`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analyze.py  (append)
def test_analyze_scope_threads_aesthetics_flags(tmp_path):
    from composerv.clarity.analyze import analyze_scope
    from composerv.store.db import Store
    from composerv.index.probe import MediaInfo

    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=10.0), proxy_path="/p/a.mp4")
    seen = {}

    def fake_aes(proxy, dur, *, aes_fps=2.0):
        seen["fps"] = aes_fps
        return (1.0, [(1.0, 0.5, False)])

    analyze_scope(s, ["/m/a.mp4"], visual_fn=lambda p, d: [(0.0, "x")], speech_fn=lambda p: [],
                  aesthetics_fn=fake_aes, aes_fps=4.0)
    assert seen["fps"] == 4.0
    assert s.get_clip_aesthetics("/m/a.mp4").best_t == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_analyze.py::test_analyze_scope_threads_aesthetics_flags -v`
Expected: FAIL with `TypeError: analyze_scope() got an unexpected keyword argument 'aesthetics_fn'`

- [ ] **Step 3: Thread the params through `analyze_scope`**

In `composerv/clarity/analyze.py`, update `analyze_scope` to accept and forward the params:

```python
def analyze_scope(
    store,
    paths: list[str],
    *,
    visual_fn: Callable[[str, float], list] | None = None,
    speech_fn: Callable[[str], list] | None = None,
    aesthetics_fn: Callable | None = None,
    aes_fps: float = 2.0,
    enable_aesthetics: bool = True,
    cooldown_s: float = 0.0,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> list[tuple[str, int, int]]:
    """Analyze every clip in the scope, caching each into the store. Returns [(path, n_moments,
    n_sentences)]. cooldown_s sleeps between clips so the GPU idles and the fans stay calm."""
    import time

    out = []
    for i, p in enumerate(paths):
        if i and cooldown_s > 0:
            time.sleep(cooldown_s)
        nv, ns = analyze_clip(store, p, visual_fn=visual_fn, speech_fn=speech_fn,
                              aesthetics_fn=aesthetics_fn, aes_fps=aes_fps,
                              enable_aesthetics=enable_aesthetics)
        out.append((p, nv, ns))
        if on_progress:
            on_progress(p, nv, ns)
    return out
```

- [ ] **Step 4: Add the CLI options**

In `composerv/cli/main.py`, update the `analyze` command signature and the `analyze_scope` call:

```python
@app.command()
def analyze(
    scope: str = typer.Argument("selected", help="selected | all | a capture-date prefix"),
    db: str = typer.Option("composerv.db", help="index database path"),
    cooldown: float = typer.Option(0.0, help="idle seconds between clips (keeps fans quiet)"),
    aes_fps: float = typer.Option(2.0, help="aesthetics scoring frame rate (frames/sec)"),
    no_aesthetics: bool = typer.Option(False, "--no-aesthetics", help="skip on-device aesthetics scoring"),
) -> None:
    """Run perception (local VLM + Whisper + on-device aesthetics) once per clip and CACHE it.
    Slow + GPU-heavy — run me under `taskpolicy -b` (+ --cooldown) to keep the fans quiet."""
    from composerv.clarity.analyze import analyze_scope
    from composerv.render.montage_out import resolve_scope
    from composerv.store.db import Store

    store = Store(db)
    paths = resolve_scope(store, scope)
    if not paths:
        typer.echo(f"no clips for scope '{scope}'")
        return
    typer.echo(f"analyzing {len(paths)} clips (local VLM + Whisper) — cache for the director…")
    analyze_scope(store, paths, cooldown_s=cooldown, aes_fps=aes_fps,
                  enable_aesthetics=not no_aesthetics,
                  on_progress=lambda p, nv, ns: typer.echo(
                      f"  {os.path.basename(p)}: {nv} moments, {ns} sentences", err=True))
    typer.echo(f"done. now: composerv montage {scope}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_analyze.py -v`
Expected: PASS

- [ ] **Step 6: Full suite + commit**

Run: `uv run pytest -q`
Expected: PASS (whole suite green)

```bash
git add composerv/clarity/analyze.py composerv/cli/main.py tests/test_analyze.py
git commit -m "feat: analyze CLI flags --aes-fps / --no-aesthetics"
```

---

## Notes for the implementer

- The Swift API names (`CalculateImageAestheticsScoresRequest`, `.perform(on:)`, `ImageAestheticsScoresObservation.overallScore` / `.isUtility`) target the macOS 15 Swift Vision API. If the binary fails to build, fix the API call guided by Task 2 Step 6's manual run — the rest of the pipeline degrades gracefully meanwhile (no tags, `best_t=None`).
- `.composerv/` and `*.db` are already gitignored; the built `.composerv/bin/aesthetics` is not committed (it is rebuilt on first use per machine).
- Real on-device scoring only runs on macOS 15+. All unit tests inject scores or a fake binary, so the suite is green on any platform; the one real-binary check (Task 2 Step 6) is manual.
