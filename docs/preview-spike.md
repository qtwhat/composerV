# Live-preview de-risk spike

Goal: validate that composerV can give a **true real-time, zero-render** preview by
playing an `AVComposition` (a virtual timeline that references the proxy clips by
in/out, with no transcode), and measure the two latencies the plan flagged as the
make-or-break unknowns:

1. **rebuild latency** — rebuilding the composition when the edit changes, and
2. **swap latency** — `replaceCurrentItem` → the new item reaching `readyToPlay`.

Plus the qualitative checks: are cuts seamless (no black flash / stall), is scrubbing
responsive at 20–40 segments, does VFR break it (it shouldn't, because proxies are CFR).

## What's already measured (headless)

`--check` builds the composition repeatedly and reports rebuild latency + frame-accurate
source ranges. The **rebuild is ~1 ms median (max ~7 ms)** — effectively free.

The other half, **swap→ready** (`replaceCurrentItemWithPlayerItem:` → the new item
reaching `readyToPlay`), was measured headlessly with a manual run-loop pump (no window
needed for an AVPlayer to load an item): **~62 ms steady-state, ~200 ms on the first
(cold) reload, all `status=1` (success).** So a full edit→preview cycle is ~62 ms,
comfortably under the 200 ms target. **AVComposition is viable as the v1 engine.** A real
AVPlayerView window adds a small first-frame draw on top; the GUI run below confirms that
and lets you judge seamlessness/scrub, which headless can't show.

## Run it on the M4 Max

Generate a few uniform CFR test clips and an EDL, then open the player.

```sh
cd <repo>

# 1) make synthetic CFR clips (red/440Hz "A", green/660Hz "B") — uses ffmpeg
uv run python - <<'PY'
from composerv.devtools import make_cfr_test_clip
make_cfr_test_clip("/tmp/A.mp4", seconds=6, label="A", tone_hz=440)
make_cfr_test_clip("/tmp/B.mp4", seconds=6, label="B", tone_hz=660)
import json
json.dump({"fps":30,"clips":[
  {"kind":"clip","file":"/tmp/A.mp4","in":1.0,"out":3.0},
  {"kind":"clip","file":"/tmp/B.mp4","in":0.5,"out":2.5},
  {"kind":"clip","file":"/tmp/A.mp4","in":4.0,"out":5.0},
]}, open("/tmp/edl.json","w"))
print("wrote /tmp/edl.json")
PY

# 2a) headless sanity + rebuild latency
uv run composerv preview /tmp/edl.json --check

# 2b) GUI window, live-reload: edit /tmp/edl.json in another editor and watch it update,
#     with build + swap->ready latency printed to the terminal on each change
uv run composerv preview /tmp/edl.json --watch

# 2c) stress: apply 50 randomized re-edits back-to-back and print the latency distribution
uv run composerv preview /tmp/edl.json --stress 50
```

## What to look for (the spike's pass/fail)

- **Swap latency**: terminal prints `reload: build=…ms  swap->ready=…ms` on each reload.
  Target: perceived < ~200 ms. If it's large, we fall back to mpv or rethink the swap.
- **Seamlessness**: at each cut (clip A red ↔ clip B green, distinct tones) is there a
  black flash, freeze, or audio gap? A known AVPlayer risk is the item-swap black blink;
  note if it appears.
- **Scrub**: drag the scrubber across cut boundaries — is it responsive and accurate?
- **Frame accuracy**: the burned-in counter (if your ffmpeg has `drawtext`) / `testsrc`
  timestamp should show the cut landing on the requested in/out.

Report the swap-latency numbers and any blink/stall; that decides whether AVComposition
is the v1 engine or we switch to the mpv fallback.
