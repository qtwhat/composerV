# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Bilingual README (English and Chinese) with an architecture diagram and a per-stage
  status table.
- English version of the interactive pipeline page (`index.en.html`) with an EN / 中文
  language toggle on both pages.
- Social preview card and Open Graph / Twitter meta for the GitHub Pages site.
- Continuous integration: ruff lint on Linux and the test suite on macOS.

### Fixed
- Declared `pillow` as a core dependency. It was imported by `clarity`, `reframe`, and
  `faces` but never declared, so a clean install had no Pillow.

## [0.1.0] - 2026-07-04

First public release.

### Added
- End-to-end pipeline in five stages, all around one SQLite store:
  `catalog` (ingest) -> `analyze` -> `confirm` -> `montage` (director) -> `preview` / `export`.
- Local perception: per-frame VLM captions/objects/OCR, WhisperX transcript, and
  on-device (Apple Vision) aesthetic scoring. Runs once and is cached in the store.
- Director: a single Claude call over a text footage table that returns the edit plus a
  music intent.
- Deterministic music selection (`rank_tracks`) with beat-snapping.
- Auto-reframe: crops vertical clips to fill 16:9 while tracking the subject.
- Zero-render AVComposition live preview and a hand-rolled FCPXML 1.13 exporter for
  Final Cut Pro.
- `composerv demo`: a fully synthetic demo set (footage plus beat-gridded music) so the
  pipeline can be tried with no downloads and no personal media.
- Backend routing for the director: the `claude` CLI when installed, otherwise the
  Anthropic API via `ANTHROPIC_API_KEY`.

[Unreleased]: https://github.com/qtwhat/composerV/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/qtwhat/composerV/releases/tag/v0.1.0
