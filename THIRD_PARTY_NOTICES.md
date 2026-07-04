# Third-party models and licenses

composerV's code is MIT-licensed (see LICENSE). At runtime it downloads and runs
third-party ML models whose licenses differ from the code license. Verified
2026-07-04 against the upstream sources linked below.

| Model | Used for | Where in code | License |
|---|---|---|---|
| `mlx-community/Qwen2.5-VL-7B-Instruct-4bit` (Qwen2.5-VL-7B-Instruct) | visual understanding (captions, OCR, object grounding) | `composerv/analyze/backends/qwen_mlx.py` | Apache-2.0 |
| `mlx-community/whisper-large-v3-turbo` (OpenAI Whisper) | speech transcription | `composerv/audio/transcribe.py` | MIT |
| insightface `buffalo_l` model pack | face detection + embeddings (person naming, subject tracking for reframe) | `composerv/faces/detect.py`, `composerv/reframe/detect.py` | **Non-commercial research use only.** The insightface *code* (pip package) is MIT, but the pretrained buffalo model packs are released for non-commercial research purposes only; commercial use requires a separate license from the InsightFace team. |
| Apple Vision framework | on-device aesthetics scoring | `swift/aesthetics.swift` | macOS system framework (no redistribution; Apple platform terms apply) |

## The insightface caveat, spelled out

Everything face-related in composerV (the `faces` / `name` / `merge` CLI, the
CONFIRM stage's person naming, and subject tracking in auto-reframe) runs on the
buffalo_l pretrained pack. If you use composerV commercially, that one component
is not covered by this repo's MIT license: either obtain a commercial model
license from InsightFace, swap in a commercially-licensed face model, or run
with face features disabled (they are optional; the rest of the pipeline works
without them).

Sources: [insightface README (model licensing)](https://github.com/deepinsight/insightface),
[buffalo pricing issue #2587](https://github.com/deepinsight/insightface/issues/2587),
[Qwen2.5-VL-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct),
[whisper-large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo).

## Python package dependencies

Declared in `pyproject.toml`; all under standard permissive licenses (MIT /
Apache-2.0 / BSD / ISC), installed from PyPI and not redistributed here.
Music tracks are not part of this repository; the library ledger
(`docs/music-library.md`) records per-track source and license (CC0 / CC-BY /
public domain), and CC-BY attribution is written into each reel's
`*.music-rationale.json` sidecar automatically.
