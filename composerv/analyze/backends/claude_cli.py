"""Caption frames via the `claude` CLI (the user's Claude Code subscription, no API key).

Each call shells `claude -p` with a narrow tool scope (`--allowedTools Read`, no
bypass-permissions) and asks for a JSON array describing a BATCH of frames, to amortize
the per-call startup. Pure helpers (build_prompt / parse_response) are unit-tested; the
live call is validated manually.

Env notes: ANTHROPIC_API_KEY / CLAUDE_API_KEY are stripped so it always uses the
subscription, never API billing. A proxy can be supplied (some setups route claude
through one) via the `proxy` arg or the CV_CLAUDE_PROXY env var.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

from composerv.analyze.base import SHOT_TYPES, CaptionResult, register_backend

_ENUM = "|".join(SHOT_TYPES)


def build_prompt(image_paths: list[str]) -> str:
    listing = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(image_paths))
    n = len(image_paths)
    return (
        f"You are analyzing {n} video frame(s). Read these image files with the Read tool, "
        f"in this exact order:\n{listing}\n\n"
        f"Reply with ONLY a JSON array of exactly {n} objects (no prose, no markdown fence), "
        f"one per image in the same order. Each object:\n"
        f'{{"caption": "<one factual sentence>", "shot_type": "<one of: {_ENUM}>", '
        f'"objects": ["<notable objects>"], "salience": <number 0..1, how notable/interesting>}}'
    )


def _coerce(obj: dict) -> CaptionResult:
    shot = obj.get("shot_type", "unknown")
    if shot not in SHOT_TYPES:
        shot = "unknown"
    objects = obj.get("objects") or []
    if not isinstance(objects, list):
        objects = [str(objects)]
    try:
        salience = float(obj.get("salience", 0.0))
    except (TypeError, ValueError):
        salience = 0.0
    return CaptionResult(
        caption=str(obj.get("caption", "")),
        shot_type=shot,
        objects=[str(o) for o in objects],
        ocr_text=str(obj.get("ocr_text", "")),
        salience=salience,
    )


def parse_response(result_text: str, n: int) -> list[CaptionResult]:
    """Parse the model's text into exactly n CaptionResults; tolerant of fences/prose.

    Missing/garbled items are padded with an error placeholder so the output stays aligned
    with the input frames (callers can detect failures by the leading '[' marker)."""
    placeholder = CaptionResult(caption="[no caption]")
    items: list = []
    text = result_text.strip()
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, list):
                items = parsed
        except json.JSONDecodeError:
            items = []

    out = [_coerce(it) if isinstance(it, dict) else placeholder for it in items[:n]]
    out += [placeholder] * (n - len(out))
    return out


def claude_text(prompt: str, model: str = "claude-sonnet-4-6", timeout: int = 300,
                proxy: str | None = None) -> str:
    """Run a text-only Claude call and return the result text (for story reasoning).

    Backend order: the `claude` CLI when installed (the subscription; API keys stripped;
    proxy via arg or CV_CLAUDE_PROXY), else the Anthropic API via ANTHROPIC_API_KEY
    (claude_api.api_text). Set CV_CLAUDE_BACKEND=api to force the API over the CLI.
    """
    binp = shutil.which("claude")
    if os.environ.get("CV_CLAUDE_BACKEND", "").lower() == "api" or not binp:
        if os.environ.get("ANTHROPIC_API_KEY"):
            from composerv.analyze.backends.claude_api import api_text

            return api_text(prompt, model=model, timeout=timeout)
        if binp:
            print("[claude_text] CV_CLAUDE_BACKEND=api but ANTHROPIC_API_KEY is not set",
                  file=sys.stderr)
            return ""
        print("[claude_text] no `claude` CLI on PATH and no ANTHROPIC_API_KEY — "
              "install Claude Code, or set ANTHROPIC_API_KEY (uv sync --extra analyze-api)",
              file=sys.stderr)
        return ""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("CLAUDE_API_KEY", None)
    p = proxy if proxy is not None else os.environ.get("CV_CLAUDE_PROXY")
    if p:
        env["http_proxy"] = env["https_proxy"] = p
    try:
        proc = subprocess.run(
            [binp, "-p", prompt, "--output-format", "json", "--model", model],
            capture_output=True, text=True, env=env, timeout=timeout,
        )
        obj = json.loads(proc.stdout)
        if obj.get("is_error") or obj.get("api_error_status"):
            print(f"[claude_text] api error: {obj.get('api_error_status')}", file=sys.stderr)
        return obj.get("result", "")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        # do NOT swallow silently — a caller that gets "" should know it was a failure, not empty
        print(f"[claude_text] call failed: {type(e).__name__}", file=sys.stderr)
        return ""


def claude_read(prompt: str, model: str = "claude-sonnet-4-6", timeout: int = 600,
                proxy: str | None = None) -> str:
    """Run `claude -p` WITH the Read tool (so it can read frame image paths named in the
    prompt) and return the result text. For clip-level video understanding over a frame
    sequence. Uses the subscription (API keys stripped); proxy via arg or CV_CLAUDE_PROXY."""
    binp = shutil.which("claude")
    if not binp:
        return ""
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("CLAUDE_API_KEY", None)
    p = proxy if proxy is not None else os.environ.get("CV_CLAUDE_PROXY")
    if p:
        env["http_proxy"] = env["https_proxy"] = p
    try:
        proc = subprocess.run(
            [binp, "-p", prompt, "--output-format", "json", "--allowedTools", "Read", "--model", model],
            capture_output=True, text=True, env=env, timeout=timeout,
        )
        return json.loads(proc.stdout).get("result", "")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return ""


class ClaudeCliBackend:
    name = "claude-cli"

    def __init__(self, model: str = "claude-haiku-4-5", batch_size: int = 8,
                 timeout: int = 300, proxy: str | None = None, inter_call_delay: float = 0.3):
        self.model = model
        self.batch_size = batch_size
        self.timeout = timeout
        self.proxy = proxy if proxy is not None else os.environ.get("CV_CLAUDE_PROXY")
        self.inter_call_delay = inter_call_delay
        self._bin = shutil.which("claude")

    def _env(self) -> dict:
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("CLAUDE_API_KEY", None)
        if self.proxy:
            env["http_proxy"] = env["https_proxy"] = self.proxy
        return env

    def _run_batch(self, image_paths: list[str]) -> list[CaptionResult]:
        if not self._bin:
            return [CaptionResult(caption="[claude CLI not found]") for _ in image_paths]
        cmd = [
            self._bin, "-p", build_prompt(image_paths),
            "--output-format", "json", "--allowedTools", "Read", "--model", self.model,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, env=self._env(), timeout=self.timeout)
            envelope = json.loads(proc.stdout)
            if envelope.get("is_error"):
                return [CaptionResult(caption="[claude error]") for _ in image_paths]
            return parse_response(envelope.get("result", ""), len(image_paths))
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            return [CaptionResult(caption="[claude call failed]") for _ in image_paths]

    def caption_frames(self, image_paths: list[str]) -> list[CaptionResult]:
        out: list[CaptionResult] = []
        for i in range(0, len(image_paths), self.batch_size):
            batch = image_paths[i : i + self.batch_size]
            out.extend(self._run_batch(batch))
            if i + self.batch_size < len(image_paths):
                time.sleep(self.inter_call_delay)
        return out


register_backend("claude-cli", ClaudeCliBackend)
