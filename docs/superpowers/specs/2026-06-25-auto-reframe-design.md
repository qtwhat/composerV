# Auto-reframe: subject-tracking dynamic crop for vertical clips in a horizontal reel

Date: 2026-06-25 · Status: design approved + EMPIRICALLY HARDENED (workflow wf_297d4bd2). Ready for implementation plan.

## Problem

The reel canvas is 16:9. Vertical (9:16) clips currently pillarbox — a narrow centered strip with
big black bars — which the user finds uncomfortable. (A prior bug also left-aligned them.) We want
vertical content to **fill** the frame by cropping a 16:9 window that **follows the subject** and
moves **smoothly** over time (auto-reframe).

## Goals / non-goals

- **Goal:** for every clip whose DISPLAY aspect ≠ the canvas (vertical clips), automatically crop-to-
  fill a 16:9 window that tracks the subject, smoothed (no jitter, no fast jumps). Priority
  **face → person → center**.
- **Goal (free win):** the same per-segment-transform machinery makes the composition robust to
  native-aspect sources — non-reframed mismatched clips are correctly **centered** (FIT/pillarbox),
  not left-aligned; and ROTATED sources render upright (today they render sideways — a latent bug).
- **Non-goals (v2):** director deciding reframe per clip (v1 is automatic); a dense dedicated
  person/body detector (v1's "person" tier reuses coarse stored boxes); landscape-into-vertical;
  cinematic speed-ramps; reading the original instead of a proxy at export for non-reframe clips.

## Empirically-verified facts that shape the design (workflow wf_297d4bd2, all rendered/measured)

1. **AVFoundation layer-instruction transform space is TOP-LEFT origin, +y DOWN** (5 renders).
   `ty>0` moves content down-screen. This is the SAME convention insightface/PIL face coords use, so
   crop rects (top-left source pixels) flow straight through `fill_transform`/`fit_transform` with
   **NO sign flip**. (The date-stamp `make_date_stamp_layer` CALayer uses the OPPOSITE convention —
   CoreAnimation bottom-left/+y-up — because it's an `AVVideoCompositionCoreAnimationTool`, a
   different space. Do not conflate.)
2. **Rotated sources.** Phones code portrait as 1920×1080 + a 90° `preferredTransform`; `probe.py`
   reads the CODED size, so the aspect test must use the **oriented (display) size** =
   `naturalSize ⊗ preferredTransform` (via `CGRectApplyAffineTransform(...).size`, take abs). And
   `build_composition` never copies `preferredTransform`, so rotated sources currently render
   SIDEWAYS; the per-segment transform must be `CGAffineTransformConcat(orient, reframe_affine)`
   where `orient = concat(preferredTransform, translate(-rect.origin))` (the origin-normalize is
   required — without it a 90° rotation renders all-black, verified). For unrotated clips
   `orient = identity`, so the landscape path is unchanged.
3. **renderSize is only the composition working space; the export PRESET governs output pixels.**
   960×540 preset + 1280×720 renderSize → a 960×540 file (verified). Use
   `AVAssetExportPreset1280x720` so renderSize == output and it matches the 1280×720 proxy canvas.
   The canvas must be a SINGLE value threaded into BOTH `setRenderSize_` AND `_attach_title_overlay`
   (today the overlay uses `vtrack.naturalSize()` → on portrait-first reels the date stamp is
   mis-placed/clipped — verified).
4. **Per-segment transforms.** AVFoundation HOLDS the last keyed transform across a segment boundary
   on one layer instruction (verified) → set an explicit `setTransform_atTime_` at EVERY segment's
   first frame. `clip_layout` must mirror `build_composition`'s frame-snapped integer-frame cursor
   (raw float seconds drift ~4–5 frames over ~40 clips).
5. **Reframe needs the ORIGINAL source, and only the played window.** The pillarbox proxy bakes black
   bars (useless to crop), and `intention_to_edl` writes proxy paths — so montage must put the
   ORIGINAL path on aspect-mismatched clips' EDL entries. The subject track must be computed over the
   clip's `[in,out]` window only (and cached per-clip as a full track + sliced per use), or detection
   cost explodes: measured insightface buffalo_l ≈ **258 ms/face-frame** at 1080×1920 → a 60s clip at
   4fps ≈ 62s of silent CPU. Mitigation (combined ≈ 30×): detect `[in,out]` only, `det_size=(384,384)`,
   `fps=2–4`, cache full-clip once, emit a progress callback.

## Architecture — executor-side, decision-vs-data, no director change

Three small isolated units plus the composition hook.

### ① Subject track — `reframe/track.py` + `reframe/detect.py` (new)
For an aspect-mismatched clip, produce a dense, segment-local subject-center track in the SOURCE's
upright normalized coords `[(t, cx, cy, conf)]`.
- **Face (primary):** insightface on frames sampled at `fps` from the source's `[in,out]` window
  (ffmpeg auto-rotates, so frames are upright; normalize face bbox by the frame's own size). Pick the
  face by: a NAMED family member (cosine of the face embedding to the gallery centroid ≥ threshold)
  → else continuity (nearest to the previous window center) → else largest area. (`detect_fn` returns
  `[(bbox, embedding)]`; gallery centroids + `clip_person_ids` already in the store.)
- **Person (fallback, no face on a frame):** the stored grounded person box, matched by NEAREST time
  within a tolerance (NOT exact-time equality), held. Coarse (boxes are from the pillarbox proxy at a
  sub-1fps grid) — a hint, not precise; a dense body detector is v2.
- **Center (last resort):** `(0.5, 0.5)`, conf 0.0; keep the last face/person lock so a brief no-face
  gap doesn't snap to center.
- `detect_fn` injectable → unit-tested with fakes; live insightface validated manually.
- Cached in the store as a FULL-clip track; `slice_track(track, in, out)` rebases to segment-local.

### ② Crop path — `reframe/path.py` (new, PURE — formulas verified correct)
From `[(t, cx, cy)]` + UPRIGHT source size + target aspect, a smoothed per-time crop window.
- Window = the cover window (largest target-aspect rect filling one full source dimension).
- Center on the subject, **clamped** to source bounds; **smooth** = EMA + max-speed cap + dead-zone.
- `fit_transform`/`fill_transform` map source crop rects (top-left pixels) into the canvas — verified
  no sign flip; they consume the ORIENTED size (rotation lives only in `orient_transform`, so these
  pure modules stay rotation-agnostic).

### ③ Apply — `render/preview/composition.build_video_composition` (extend)
Aspect-aware per-segment transform on the single layer instruction.
- ONE canvas value (`1280×720`) → `setRenderSize_` AND `_attach_title_overlay`.
- For each `(start_s, dur_s, clip)` in the frame-snapped `clip_layout`: layer transform =
  `CGAffineTransformConcat(orient_transform(file), reframe_affine)`, where reframe_affine is
  `fit_transform(oriented_size)` centered (mismatch, no reframe → fixes left-align) or a
  `fill_transform` ramp across the segment following the crop path (reframe), or identity-ish
  scale-to-fill (landscape). Set an explicit transform at every segment's first frame.
- Sources are native-aspect ORIGINALS. Legacy `clips=None` path unchanged.

### ④ Decision / fallback
- Automatic: any clip whose ORIENTED aspect ≠ canvas is reframed when a usable track exists; else
  centered-FIT. No director output / prompt change.

## Error handling / fallbacks
- No faces and no person boxes → center-FIT (pillarbox), never crash.
- insightface failure on a frame → no sample that frame; ② holds/interpolates.
- Rotated source with no `preferredTransform` API value → identity orient (treat as coded size).
- A landscape clip flagged by mistake → FIT ≈ identity, no harm.

## Testing
- `reframe/path.py`: pure TDD — cover window, clamp, speed-limit, dead-zone, AND a `fill_transform`
  sign test (face-low → `ty` more negative) chaining path→transform.
- `reframe/track.py`: injected fake `detect_fn` → face(gallery/continuity)→person(nearest-time)→center
  priority; `slice_track` rebasing.
- `composition`: pure `clip_layout` (frame-snapped) / `fit_transform` / `fill_transform` tests; the
  AVFoundation transform application + orientation + overlay validated by a LIVE export of BOTH a
  genuine portrait clip AND a rotated phone-style clip (the DJI clips have identity rotation and hide
  B2 — the live gate MUST include a non-identity `preferredTransform` source).

## Open v2 items (out of scope)
- Dense dedicated person/body detector for the "person" tier (back-of-head, no face).
- Director decides reframe per clip (output field) when some vertical clips read better pillarboxed.
- `probe.py` reading the displaymatrix so non-render consumers also see oriented dims.
- Tunable smoothing presets (lock-on vs gentle drift).
