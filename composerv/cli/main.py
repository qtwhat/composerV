"""composerV command-line surface (also what the Claude Code skill drives).

Minimal for now: `version` and `preview`. index / story / export land here as they're built.
"""

from __future__ import annotations

import os

import typer

from composerv.render.outputs import default_db, default_music_dir

app = typer.Typer(help="composerV: local, story-first video editing assistant", no_args_is_help=True)

music_app = typer.Typer(help="Music library tools (offline feature indexing).")
app.add_typer(music_app, name="music")


@app.command()
def version() -> None:
    """Print the composerV version."""
    from composerv import __version__

    typer.echo(__version__)


@app.command()
def demo(
    directory: str = typer.Argument("composerv-demo", help="where to write the demo set"),
    footage_seconds: float = typer.Option(6.0, help="length of each generated clip"),
) -> None:
    """Generate a synthetic demo set (footage + beat-gridded music) — try the pipeline
    with zero downloads and no personal media."""
    from composerv.devtools import make_demo_set

    out = make_demo_set(directory, footage_seconds=footage_seconds)
    for p in out["footage"] + out["music"]:
        typer.echo(f"  wrote {p}")
    for note in out["skipped"]:
        typer.echo(f"  skipped {note}", err=True)
    db = os.path.join(directory, "demo.db")
    typer.echo("\nnext:")
    typer.echo(f"  composerv catalog {directory}/footage --db {db} --work-dir {directory}/.composerv")
    typer.echo(f"  composerv music index {directory}/music")
    typer.echo(f"  composerv analyze all --db {db}")
    typer.echo(f"  composerv montage all --db {db} --music-dir {directory}/music")


@app.command()
def preview(
    edl: str = typer.Argument(..., help="path to an EDL JSON file"),
    fps: int = typer.Option(None, help="override timeline fps"),
    check: bool = typer.Option(False, help="headless: report duration + rebuild latency, no window"),
    iterations: int = typer.Option(20, help="rebuilds to time in --check mode"),
    watch: bool = typer.Option(False, help="GUI: reload when the EDL file changes"),
    stress: int = typer.Option(0, help="GUI: apply N randomized re-edits and time them"),
    loop: bool = typer.Option(False, help="GUI: loop one composition with no swaps (isolate cut seams)"),
) -> None:
    """Live-preview an edit list (zero-render AVComposition), or measure rebuild latency."""
    from composerv.render.preview import player

    if check:
        raise SystemExit(player.check(edl, fps, iterations))
    raise SystemExit(player.run_gui(edl, fps, watch, stress, loop))


@app.command()
def export(
    edl: str = typer.Argument(..., help="path to an EDL JSON file (e.g. a montage part)"),
    out: str = typer.Option(None, help="output .mp4 (default: <edl> with a .lite.mp4 suffix)"),
    title: str = typer.Option(None, help="date/event to burn in (default: the EDL's own title)"),
    db: str = typer.Option(default_db(), help="index database path (for reframe tracks + gallery)"),
    preset: str = typer.Option("AVAssetExportPreset1280x720", help="export preset (size/quality)"),
) -> None:
    """Export a reel to a LITE MP4: music + ducking + fade-out + a burned-in date stamp."""
    from composerv.render.export import export_mp4
    from composerv.render.preview.edl import load_edl_file
    from composerv.store.db import Store

    clips, fps, music, edl_title = load_edl_file(edl)
    if not out:
        from composerv.render.outputs import out_path
        stem = os.path.basename(edl)
        stem = stem[: -len(".edl.json")] if stem.endswith(".edl.json") else os.path.splitext(stem)[0]
        out = out_path("mp4", stem + ".lite.mp4")  # ~/Movies/composerV/mp4/<date>/
    stamp = title if title is not None else edl_title
    store = Store(db)
    typer.echo(f"exporting {len(clips)} clips -> {out} (preset {preset}) ...")
    path = export_mp4(clips, fps, music, out, title=stamp, preset=preset, store=store)
    typer.echo(f"  wrote {os.path.abspath(path)} · {os.path.getsize(path) / 1e6:.1f} MB")


# --- clarity + selection layer ---


@app.command()
def catalog(
    directory: str = typer.Argument(None, help="folder of clips to ingest (omit to just re-render)"),
    db: str = typer.Option(default_db(), help="index database path (default: ~/Movies/composerV)"),
    out: str = typer.Option("catalog.html", help="output HTML file"),
    work_dir: str = typer.Option(".composerv", help="where proxies + keyframes are written"),
    limit: int = typer.Option(None, help="only the first N clips, oldest first"),
    force: bool = typer.Option(False, help="re-summarize clips already done"),
    cloud: bool = typer.Option(False, help="describe with Claude (cloud) instead of the local model"),
) -> None:
    """Ingest a folder (local descriptions + keyframes) and render the clip-clarity catalog."""
    from composerv.clarity.catalog import build_cards, render_catalog
    from composerv.clarity.ingest import ingest_dir
    from composerv.store.db import Store

    store = Store(db)
    summarize = None
    if cloud:
        from composerv.clarity.summarize import claude_describe, summarize_clip

        def summarize(proxy, dur):  # noqa: E731 - small local adapter
            return summarize_clip(proxy, dur, run=claude_describe, source="claude")

    if directory:
        n = ingest_dir(directory, store, work_dir=work_dir, summarize=summarize,
                       limit=limit, force=force, log=typer.echo)
        typer.echo(f"ingested {n} clip(s)")
    cards = build_cards(store)
    with open(out, "w") as f:
        f.write(render_catalog(cards, title=directory or os.path.basename(db)))
    typer.echo(f"catalog: {os.path.abspath(out)}  ({len(cards)} clips)")


@app.command()
def select(
    ids: list[str] = typer.Argument(..., help="clip filenames or paths"),
    db: str = typer.Option(default_db(), help="index database path (default: ~/Movies/composerV)"),
) -> None:
    """Add clips to the working set."""
    from composerv.clarity.actions import set_selection
    from composerv.store.db import Store

    store = Store(db)
    paths = set_selection(store, list(ids), True)
    typer.echo(f"selected {len(paths)}; working set now {len(store.list_selected())}")


@app.command()
def unselect(
    ids: list[str] = typer.Argument(..., help="clip filenames or paths"),
    db: str = typer.Option(default_db(), help="index database path (default: ~/Movies/composerV)"),
) -> None:
    """Remove clips from the working set."""
    from composerv.clarity.actions import set_selection
    from composerv.store.db import Store

    store = Store(db)
    paths = set_selection(store, list(ids), False)
    typer.echo(f"unselected {len(paths)}; working set now {len(store.list_selected())}")


@app.command()
def selected(db: str = typer.Option(default_db(), help="index database path (default: ~/Movies/composerV)")) -> None:
    """List the current working set."""
    from composerv.store.db import Store

    sel = Store(db).list_selected()
    for p in sel:
        typer.echo(os.path.basename(p))
    typer.echo(f"-- {len(sel)} selected --")


@app.command()
def refine(
    clip_id: str = typer.Argument(..., help="clip filename or path"),
    db: str = typer.Option(default_db(), help="index database path (default: ~/Movies/composerV)"),
) -> None:
    """Re-describe one clip with Claude (cloud) for a sharper summary. Only this clip's frames go up."""
    from composerv.clarity.actions import refine_clip
    from composerv.store.db import Store

    cs = refine_clip(Store(db), clip_id)
    typer.echo(f"refined ({cs.source}): {cs.text}")


@app.command()
def analyze(
    scope: str = typer.Argument("selected", help="selected | all | a capture-date prefix"),
    db: str = typer.Option(default_db(), help="index database path (default: ~/Movies/composerV)"),
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


@app.command()
def montage(
    scope: str = typer.Argument("selected", help="selected | all | a capture-date prefix e.g. 2026-01-01"),
    db: str = typer.Option(default_db(), help="index database path (default: ~/Movies/composerV)"),
    out: str = typer.Option("", help="output NAME (routed to ~/Movies/composerV/<type>/<date>) or an explicit path prefix; default = event or scope"),
    feeling: str = typer.Option(None, help="override the inferred mood (e.g. calm/nostalgic/upbeat/sad)"),
    repeat: int = typer.Option(1, help="how many times each clip may appear in the reel"),
    music_dir: str = typer.Option(default_music_dir(), help="folder of <feeling>/ tagged music (default: ~/.composerv/music, or CV_MUSIC_DIR)"),
    event: str = typer.Option("", help="event name for the storyboard, e.g. '元旦武夷山之旅'"),
    max_part_min: float = typer.Option(5.0, help="max minutes per part; longer footage splits into parts"),
    legacy: bool = typer.Option(False, "--legacy", help="use the old rule-based brain instead of the LLM director"),
    target_min: float = typer.Option(2.0, help="director: target reel length in minutes (it curates to fit)"),
) -> None:
    """Assemble a memory reel from a scope of clips and write preview EDL + FCPXML + storyboard.
    By default the LLM director edits from the footage table (perception → director); --legacy
    uses the old rule-based assembler. Footage longer than --max-part-min splits by day."""
    from composerv.render.montage_out import resolve_scope, write_montage_outputs
    from composerv.store.db import Store

    store = Store(db)
    paths = resolve_scope(store, scope)
    if not paths:
        typer.echo(f"no clips for scope '{scope}' (try: selected | all | a date like 2026-01-01)")
        return
    if legacy:
        from composerv.music.montage import build_montage
        plans = build_montage(store, paths, music_dir=os.path.expanduser(music_dir),
                              feeling=feeling, repeat=repeat, max_part_s=max_part_min * 60.0)
    else:
        from composerv.director.montage import build_director_montage
        typer.echo("director: building footage table (local VLM + Whisper) then editing — this takes a while…")
        try:
            plans = build_director_montage(store, paths, music_dir=os.path.expanduser(music_dir),
                                           feeling=feeling, budget_s=target_min * 60.0,
                                           max_part_s=max_part_min * 60.0,
                                           brief=store.get_brief(scope))
        except RuntimeError as e:
            typer.echo(f"director failed: {e}  (try --legacy for the rule-based assembler)", err=True)
            raise typer.Exit(1) from e
    base_name = out or event or scope or "montage"   # routed unless it's an explicit path
    multi = len(plans) > 1
    for i, plan in enumerate(plans, 1):
        prefix = f"{base_name}_part{i}" if multi else base_name
        title = f"{event} · {plan.label}".strip(" ·") if (event and plan.label) else (event or plan.label)
        outs = write_montage_outputs(plan, store, prefix, event=title)
        dur = sum(s.duration_s for s in plan.intention.segments)
        track = os.path.basename(plan.track) if plan.track else "(none)"
        head = f"part {i}/{len(plans)} · " if multi else ""
        typer.echo(f"{head}{plan.label or 'montage'}: {len(plan.intention.segments)} shots · "
                   f"{dur:.0f}s · feeling={plan.feeling} · track={track}")
        for kind, p in outs.items():
            typer.echo(f"  {kind}: {os.path.abspath(p)}")


# --- faces: naming / review ---


@app.command()
def faces(
    db: str = typer.Option(default_db(), help="index database path (default: ~/Movies/composerV)"),
    out: str = typer.Option("people.html", help="output contact-sheet HTML"),
    min_clips: int = typer.Option(1, help="only show people appearing in >= N clips"),
) -> None:
    """Render the people contact sheet (most-recurring first) to name your family."""
    from composerv.faces.review import person_rows, render_face_contactsheet
    from composerv.store.db import Store

    rows = person_rows(Store(db), min_clips=min_clips)
    with open(out, "w") as f:
        f.write(render_face_contactsheet(rows, title="composerV people"))
    typer.echo(f"people: {os.path.abspath(out)}  ({len(rows)} shown)")


@app.command()
def name(
    person_id: int = typer.Argument(..., help="person id from the contact sheet"),
    person_name: str = typer.Argument(..., help="who this is"),
    db: str = typer.Option(default_db(), help="index database path (default: ~/Movies/composerV)"),
    sensitive: bool = typer.Option(False, help="gate this person from auto-inclusion (deceased/etc.)"),
) -> None:
    """Name a person; their centroid (already stored) becomes part of the family gallery."""
    from composerv.store.db import Store

    Store(db).set_person_name(person_id, person_name, sensitive=sensitive)
    typer.echo(f"named {person_id} = {person_name}{' (sensitive)' if sensitive else ''}")


@app.command()
def merge(
    into: int = typer.Argument(..., help="the person id to keep"),
    ids: list[int] = typer.Argument(..., help="person ids to merge into it (same person, split)"),
    db: str = typer.Option(default_db(), help="index database path (default: ~/Movies/composerV)"),
) -> None:
    """Merge split clusters of the same person into one (recomputes the gallery centroid)."""
    from composerv.faces.enroll import merge_persons
    from composerv.store.db import Store

    merge_persons(Store(db), list(ids), into)
    typer.echo(f"merged {list(ids)} into {into}")


@app.command()
def confirm(
    scope: str = typer.Argument("selected", help="selected | all | a capture-date prefix"),
    db: str = typer.Option(default_db(), help="index database path (default: ~/Movies/composerV)"),
    port: int = typer.Option(0, help="local server port (0 = pick a free one)"),
    no_detect: bool = typer.Option(False, "--no-detect", help="skip face detection, only re-cluster + form"),
) -> None:
    """Confirm detected people (name + note, skippable) and write a per-scope user brief.
    Opens a local browser form; the brief + person notes are fed to the director next."""
    from composerv.confirm import server as confirm_server
    from composerv.confirm.enroll_glue import default_detect_cluster, ensure_faces
    from composerv.render.montage_out import resolve_scope
    from composerv.store.db import Store

    store = Store(db)
    paths = resolve_scope(store, scope)
    if not paths:
        typer.echo(f"no clips for scope '{scope}' (try: selected | all | a date like 2026-01-01)")
        return
    if not no_detect:
        detect_fn, cluster_fn = default_detect_cluster(store)
        d, c = ensure_faces(store, paths, detect_fn=detect_fn, cluster_fn=cluster_fn, log=typer.echo)
        typer.echo(f"faces: {d} new detected · {c} people")
    confirm_server.serve_confirm(store, scope, port=port, log=typer.echo)
    typer.echo(f"saved. now: composerv montage {scope}")


@music_app.command("index")
def music_index(
    directory: str = typer.Argument(..., help="folder of audio files to index (recurses)"),
) -> None:
    """Compute + cache per-track audio features (tempo, beats, 16-point energy curve, mode,
    valence) as sibling *.features.json sidecars. Recomputes only stale/missing sidecars and
    preserves each track's source/license across re-indexing."""
    from composerv.music.features import index_music_dir

    n = index_music_dir(directory, on_progress=lambda f: typer.echo(f"  indexed {f}"))
    typer.echo(f"indexed {n} file(s)")


if __name__ == "__main__":
    app()
