"""DESIGN-TIME ONLY three-subagent collaboration (spec §6-7, protocol C). Never imported by the
shipped pipeline (spec D6). Drives director / scorer / evaluator roles to tune the prompts +
scoring weights. The scorer calls the SHIPPED rank_tracks so the tuned weights are the ones that
ship; the LLM only authors the why-match/why-not + verdict prose. Each role = prompt builder +
injectable run (defaults to claude_text) + tolerant parser, like music/mood.py.

The eval is HYBRID (spec §6.3/§7): the evaluator subagent auto-judges each round, but final
acceptance ('三方满意') also needs a human watching+listening to a real render at the 关键节点.
HumanCheckpoint reserves that slot on every round record; auto-convergence != final acceptance.
"""

from __future__ import annotations

import json
import os

from pydantic import BaseModel, Field

from composerv.models import MusicIntent, TrackFeatures
from composerv.music.score import ACCEPT_THRESHOLD, rank_tracks


class Candidate(BaseModel):
    rank: int
    path: str
    track_name: str = ""
    match_score: float = 0.0
    match_breakdown: dict = Field(default_factory=dict)
    audio_reality: dict = Field(default_factory=dict)
    why_match: str = ""
    why_not: str = ""


class Verdict(BaseModel):
    passed: bool = False
    reasons: list[str] = Field(default_factory=list)
    axes: dict = Field(default_factory=dict)
    ask: str = ""
    library_gap: bool = False


class HumanCheckpoint(BaseModel):
    """The human half of the hybrid eval (spec §6.3/§7). Left unreviewed by the auto loop; a
    person fills it after watching+listening to a real render at the 关键节点."""

    reviewed: bool = False
    approved: bool | None = None
    annotation: str = ""


class RoundRecord(BaseModel):
    round_no: int
    reel_id: str
    intent: MusicIntent
    candidates: list[Candidate] = Field(default_factory=list)
    director_reaction: dict = Field(default_factory=dict)
    verdict: Verdict
    human_checkpoint: HumanCheckpoint = Field(default_factory=HumanCheckpoint)
    converged: bool = False


def _default_run(prompt: str) -> str:
    from composerv.analyze.backends.claude_cli import claude_text

    # Design-time roles run on the SAME model as the production director (Opus 4.6) so the
    # negotiation's director judgment mirrors what ships (montage.DIRECTOR_MODEL).
    return claude_text(prompt, model="claude-opus-4-6", timeout=600)


def _json_obj(text: str):
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e <= s:
        return {}
    try:
        return json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return {}


def _json_list(text: str):
    s, e = text.find("["), text.rfind("]")
    if s == -1 or e <= s:
        return []
    try:
        return json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return []


# --- director role ---------------------------------------------------------

def build_director_intent_prompt(table_text: str, prior_ask: str = "") -> str:
    ask = f"\nThe evaluator asked you to revise: {prior_ask}\n" if prior_ask else ""
    return (
        "You are the editor. You read only descriptions; you cannot hear or see. Describe the "
        "music this reel wants as a MusicIntent JSON: a 16-point energy_curve (0..1, open to "
        "close), tempo_lo/tempo_hi (0=any), mode_pref (major|minor|any), valence (0..1), "
        f"target_duration_s, arc_text.{ask}\n\nFOOTAGE:\n{table_text}\n\nReturn one JSON object."
    )


def director_role(table_text: str, *, run=None, prior_ask: str = "") -> MusicIntent:
    run = run or _default_run
    obj = _json_obj(run(build_director_intent_prompt(table_text, prior_ask)))
    from composerv.director.prompt import _extract_intent  # tolerant; reads arc_text OR arc

    return MusicIntent(**_extract_intent(obj))


# --- scorer role -----------------------------------------------------------

def build_scorer_prose_prompt(intent: MusicIntent, ranked) -> str:
    rows = [{"path": tf.path, "score": sc, "breakdown": bd,
             "audio": {"tempo_bpm": tf.tempo_bpm, "mode": tf.mode, "valence": tf.valence,
                       "duration_s": tf.duration_s, "energy_curve": tf.energy_curve}}
            for sc, tf, bd in ranked]
    return (
        "You only read audio features, not pictures. For each scored candidate below, write "
        "why_match (where its energy curve overlaps the intent) and why_not (where it diverges). "
        f"INTENT energy_curve: {intent.energy_curve}\nCANDIDATES:\n{json.dumps(rows, indent=2)}\n\n"
        "Return a JSON array of {path, why_match, why_not}."
    )


def scorer_role(intent: MusicIntent, features_lib: list[TrackFeatures], *,
                run=None, k: int = 3) -> list[Candidate]:
    run = run or _default_run
    ranked = rank_tracks(intent, features_lib)[:k]
    prose_by_path = {}
    for item in _json_list(run(build_scorer_prose_prompt(intent, ranked))):
        if isinstance(item, dict) and item.get("path"):
            prose_by_path[item["path"]] = item
    cands = []
    for i, (score, tf, bd) in enumerate(ranked):
        pr = prose_by_path.get(tf.path, {})
        cands.append(Candidate(
            rank=i + 1, path=tf.path, track_name=os.path.basename(tf.path),
            match_score=score, match_breakdown=bd,
            audio_reality={"tempo_bpm": tf.tempo_bpm, "mode": tf.mode, "valence": tf.valence,
                           "duration_s": tf.duration_s, "energy_curve": tf.energy_curve},
            why_match=str(pr.get("why_match", "")), why_not=str(pr.get("why_not", "")),
        ))
    return cands


# --- evaluator role --------------------------------------------------------

def build_evaluator_prompt(round_repr: dict) -> str:
    return (
        "You read only the plan representation (agreed intent curve, chosen track curve, the "
        "timeline plan, frame descriptions). Judge pass/fail: is the curve aligned, are cuts "
        "sane vs beats, is the selection rationale self-consistent? "
        f"PLAN:\n{json.dumps(round_repr, indent=2, default=str)}\n\n"
        'Return JSON {pass, reasons:[...], axes:{...}, ask, library_gap}.'
    )


def evaluator_role(round_repr: dict, *, run=None) -> Verdict:
    run = run or _default_run
    obj = _json_obj(run(build_evaluator_prompt(round_repr)))
    return Verdict(
        passed=bool(obj.get("pass", False)),
        reasons=[str(r) for r in obj.get("reasons", []) or []],
        axes=obj.get("axes", {}) or {},
        ask=str(obj.get("ask", "")),
        library_gap=bool(obj.get("library_gap", False)),
    )


# --- driver + loop control -------------------------------------------------

def run_collab_round(reel_id, table_text, features_lib, runs, round_no, prior_ask=""):
    runs = runs or {}
    intent = director_role(table_text, run=runs.get("director"), prior_ask=prior_ask)
    candidates = scorer_role(intent, features_lib, run=runs.get("scorer"))
    top = candidates[0] if candidates else None
    gap = (top is None) or (top.match_score < ACCEPT_THRESHOLD)
    reaction = {"chosen_path": top.path if (top and not gap) else None,
                "revised_intent": None,
                "note": "accept top" if (top and not gap) else "no candidate over threshold"}
    round_repr = {"reel_id": reel_id, "intent": intent.model_dump(),
                  "candidates": [c.model_dump() for c in candidates],
                  "director_reaction": reaction}
    verdict = evaluator_role(round_repr, run=runs.get("evaluator"))
    if gap:
        verdict.library_gap = True
    converged = verdict.passed and reaction["chosen_path"] is not None
    return RoundRecord(round_no=round_no, reel_id=reel_id, intent=intent,
                       candidates=candidates, director_reaction=reaction,
                       verdict=verdict, converged=converged)


def negotiate(reel_id, table_text, features_lib, *, runs=None, max_rounds=4, out_dir=None):
    """Protocol-C loop (spec §7). Stops on auto-convergence (verdict pass AND director accepted in
    the SAME round) or after max_rounds (D5). Auto-convergence is NOT final acceptance: each
    record carries an unreviewed HumanCheckpoint slot for the human 关键节点 step. Writes one
    markdown transcript per round."""
    records: list[RoundRecord] = []
    prior_ask = ""
    for n in range(1, max_rounds + 1):
        rec = run_collab_round(reel_id, table_text, features_lib, runs, n, prior_ask)
        records.append(rec)
        if out_dir:
            write_round_md(out_dir, reel_id, rec)
        if rec.converged:
            break
        prior_ask = rec.verdict.ask
    return records


def write_round_md(out_dir: str, reel_id: str, rec: RoundRecord) -> str:
    reel_dir = os.path.join(out_dir, reel_id)
    os.makedirs(reel_dir, exist_ok=True)
    path = os.path.join(reel_dir, f"round-{rec.round_no:02d}.md")
    spark = "".join("▁▂▃▄▅▆▇█"[min(7, int(v * 8))] for v in (rec.intent.energy_curve or []))
    body = [
        f"# Round {rec.round_no:02d}: {reel_id}",
        "## Director intent", f"arc: {rec.intent.arc_text}", f"energy: {spark}",
        "```json", rec.intent.model_dump_json(indent=2), "```",
        "## Scorer candidates",
        "```json", json.dumps([c.model_dump() for c in rec.candidates], indent=2), "```",
        "## Director reaction", "```json", json.dumps(rec.director_reaction, indent=2), "```",
        "## Evaluator verdict", "```json", rec.verdict.model_dump_json(indent=2), "```",
        "## Human checkpoint (fill after watching the real render)",
        "```json", rec.human_checkpoint.model_dump_json(indent=2), "```",
        "## Round outcome",
        f"auto_converged={rec.converged} library_gap={rec.verdict.library_gap} "
        "(auto-converged != final acceptance; human checkpoint pending)",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(body) + "\n")
    return path
