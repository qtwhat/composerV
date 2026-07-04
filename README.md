# composerV

A local-first, **story-first** assistant for turning a large personal video archive
(GoPro / phone, hundreds of GB) into a story you believe in, then handing it off to
Final Cut Pro for finishing.

The hard, valuable part is **helping you think the story**, not search and not format
conversion. Everything below the story layer exists to serve it.

## The idea in one line

Turn video into **semantics + metadata** so an LLM can help you *curate* (dedupe, pace,
vary, select for narrative fit), not just attribute-match. You author the story spine;
the AI fills the beats; a **zero-render live preview** reflects every edit instantly;
the locked story compiles to FCPXML for Final Cut.

## Architecture (three layers)

- **`index/`** (substrate): scan → CFR 720p proxies + frame sampling (with true source
  PTS) → VLM captions / objects / OCR / shot type / WhisperX transcript / insightface
  faces / GPS+time / emotion+quality signals → SQLite + sqlite-vec + per-clip sidecars
  → a 3-tier "Archive Brief" an LLM can reason over.
- **`story/`** (the product): human authors the spine (controlling idea + target
  feeling); the AI proposes a structure and fills beats with candidate moments ranked by
  narrative importance (not "exciting-ness"); `compile(Story) → IntentionList`.
- **`render/`** (output): one IntentionList, three targets — a live **AVComposition**
  preview (zero render), a hand-rolled **FCPXML 1.13** emitter for Final Cut, and a
  storyboard / optional flattened share copy.

See the full design in `docs/` / the approved plan.

## Status

The core pipeline works end to end: perception (local VLM + Whisper + on-device
aesthetics) → director (Claude, one call over a text footage table) → preview /
MP4 / FCPXML, with real music selection (deterministic `rank_tracks` + beat snap)
and auto-reframe. In progress: a human-in-the-loop CONFIRM stage (person naming +
user brief) between perception and the director. Current state and how to resume
each line of work: `docs/HANDOFF.md`; architecture snapshot: `docs/STATUS.md`;
interactive requirements board: `composerv_pipeline_overview.html`.

## Toolchain

Python 3.12 managed by [uv](https://docs.astral.sh/uv/). Heavy ML/platform deps are
optional extras (`analyze-local`, `analyze-api`, `transcribe`, `faces`, `preview`,
`vector`) so the core installs light.

```sh
uv sync                 # core + dev
uv run pytest           # run tests
```

## Try it without your own footage

`composerv demo` generates a fully synthetic demo set (test-pattern clips with
synthesized speech and an OCR-able sign, plus two beat-gridded music tracks with
distinct energy arcs) — no downloads, no licenses, no personal media:

```sh
uv run composerv demo ./composerv-demo
# then follow the printed catalog / music index / analyze / montage commands
```

The director (montage) step needs Claude: either the `claude` CLI (a Claude Code
subscription; used automatically when installed) or an Anthropic API key
(`uv sync --extra analyze-api`, then `export ANTHROPIC_API_KEY=...`). Set
`CV_CLAUDE_BACKEND=api` to force the API when both are available. Perception
(analyze) runs fully local and needs neither.

## Configuration (env vars)

| Var | Default | Meaning |
|---|---|---|
| `CV_OUT` | `~/Movies/composerV` | output base (reels, EDL/FCPXML/storyboards) and the default DB location |
| `CV_MUSIC_DIR` | `~/.composerv/music` | the `<feeling>/`-tagged music library |
| `CV_AESTHETICS_BIN` | auto-built | path to the compiled Swift aesthetics scorer |
| `CV_CLAUDE_BACKEND` | CLI when installed | set `api` to force the Anthropic API over the `claude` CLI |
| `CV_CLAUDE_PROXY` | — | HTTP(S) proxy for `claude` CLI calls |

## License

MIT (see `LICENSE`). The ML models downloaded at runtime have their own
licenses — notably the insightface face models are **non-commercial research
only**; see `THIRD_PARTY_NOTICES.md` before any commercial use.
