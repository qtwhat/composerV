# Contributing to composerV

Thanks for your interest. composerV is a local-first, story-first video-editing
assistant for macOS / Apple Silicon. This guide covers how to set up, test, and submit
changes.

## Development setup

The project uses [uv](https://docs.astral.sh/uv/) and Python 3.12.

```sh
uv sync                 # core + dev dependencies (installs light)
uv run composerv --help
```

Heavy ML and platform features are optional extras, so the core stays small. Install
only what you need:

| Extra | Adds | Notes |
|---|---|---|
| `analyze-local` | local VLM (mlx-vlm) | per-frame perception |
| `analyze-api` | Anthropic SDK | director via API key |
| `transcribe` | mlx-whisper | speech to text |
| `faces` | insightface + onnxruntime | face naming / reframe subject tracking (models are non-commercial research only) |
| `preview` | pyobjc / AVFoundation | live preview + MP4 export, **macOS only** |
| `vector` | sqlite-vec | vector search |

Example: `uv sync --extra preview --extra transcribe`.

## Running tests

```sh
uv run pytest                 # the default suite
CV_RUN_SLOW=1 uv run pytest   # also run the slow librosa tests
```

Notes:
- Some tests need `ffmpeg` on `PATH`; they skip cleanly if it is missing.
- The preview and export tests need the `preview` extra (macOS only). Without it they
  are not collected.
- The director and ML paths are mocked in tests, so no API key or model download is
  required to run the suite.

## Linting

```sh
uv run ruff check .
```

CI runs the same check. The configuration is in `pyproject.toml`. It keeps the
real-bug rules (pyflakes) and leaves the codebase's deliberate compact style alone
(semicolon one-liners, small lambdas, longer lines). Please do not run `ruff format`
on existing files, since it would churn that style.

## Platform notes

- The core pipeline (`catalog`, `analyze`, `montage`, music) runs on macOS and Linux.
- Live preview and MP4 export use AVFoundation and are macOS only.
- The director step needs either the `claude` CLI (a Claude Code subscription) or an
  `ANTHROPIC_API_KEY`. Perception runs fully local.

## Submitting a change

1. Open an issue first for anything non-trivial, so we can agree on the approach.
2. Branch off `main`.
3. Keep the test suite green and `ruff check` clean. CI (lint on Linux, tests on
   macOS) must pass on your pull request.
4. Write a clear PR description: what changed and why. Link the issue it addresses.
5. Match the style of the surrounding code.

## Reporting bugs and requesting features

Use the issue templates. For bugs, include the command you ran, what you expected, what
happened, and your OS / Python / uv versions. See also [SECURITY.md](SECURITY.md) for
anything security-sensitive.
