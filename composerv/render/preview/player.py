"""Live preview player + latency harness (the de-risk spike).

Modes:
  --check       headless: build the composition from an EDL N times; report rebuild
                latency, total duration, and frame-accurate source ranges. No window.
  (default GUI) open an AVPlayerView window playing the composition.
    --watch     reload when the EDL file changes (rebuild -> replaceCurrentItem ->
                restore playhead); prints build + swap->ready latency per change.
    --stress N  self-driving: apply N randomized re-edits back to back and print the
                swap->ready latency distribution (no file editing needed).

Latency is measured WITHOUT KVO: a single fast timer polls AVPlayerItem.status() until
readyToPlay, which is more robust than KVO bridging in PyObjC.

EDL JSON: {"fps":30,"clips":[{"kind":"clip","file":...,"in":..,"out":..}|{"kind":"gap","duration":..}]}
"""

from __future__ import annotations

import argparse
import os
import random
import statistics
import time

from composerv.render.preview.composition import (
    build_audio_mix,
    build_composition,
    build_video_composition,
    composition_seconds,
    make_date_stamp_layer,
    video_source_ranges,
)
from composerv.render.preview.edl import load_edl_file

_TAIL_FADE_S = 1.5  # the reel fades to black + silence over its last this-many seconds

# AVPlayerItemStatus: 0 = unknown/loading, 1 = readyToPlay, 2 = failed


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return s[k]


def check(edl_path: str, fps_override: int | None, iterations: int) -> int:
    clips, fps, music, title = load_edl_file(edl_path)
    if fps_override:
        fps = fps_override

    build_ms: list[float] = []
    comp = None
    for _ in range(max(1, iterations)):
        t0 = time.perf_counter()
        comp = build_composition(clips, fps=fps, music=music)
        build_ms.append((time.perf_counter() - t0) * 1000)

    n_clips = sum(1 for c in clips if c.get("kind") != "gap")
    print(f"EDL: {len(clips)} items ({n_clips} clips), fps={fps}")
    if music:
        print(f"music bed: {os.path.basename(music['file'])} "
              f"(duck {music.get('duck_db', 0)}dB, fade {music.get('fade_out_s', 0)}s)")
    dur = composition_seconds(comp)
    print(f"composition duration: {dur:.3f}s")
    vc = build_video_composition(comp, fps=fps, tail_s=_TAIL_FADE_S)
    if vc is not None:
        print(f"ending: fade to black over the last {min(_TAIL_FADE_S, dur):.1f}s")
    print(
        f"rebuild latency over {len(build_ms)}x: "
        f"p50={_pct(build_ms, 50):.1f}ms mean={statistics.fmean(build_ms):.1f}ms "
        f"max={max(build_ms):.1f}ms"
    )
    print("video source ranges (start_s, dur_s):")
    for i, (s, d) in enumerate(video_source_ranges(comp)):
        print(f"  [{i}] start={s:.3f} dur={d:.3f}")
    return 0


def _add_date_overlay(view, text: str) -> None:
    """Show the reel's date label in the live preview (bottom-left of the video area). Uses the
    player view's content overlay, so it sits over the picture but under the transport controls."""
    overlay = view.contentOverlayView()
    if overlay is None:
        return
    overlay.setWantsLayer_(True)
    host = overlay.layer()
    if host is None:
        return
    from Quartz import CAKeyframeAnimation, kCAFillModeForwards
    stamp = make_date_stamp_layer(text, 40.0, 24.0)
    anim = CAKeyframeAnimation.animationWithKeyPath_("opacity")   # show once at the start, fade out
    anim.setValues_([1.0, 1.0, 0.0, 0.0])
    anim.setKeyTimes_([0.0, 0.63, 0.86, 1.0])
    anim.setDuration_(3.5)
    anim.setRemovedOnCompletion_(False)
    anim.setFillMode_(kCAFillModeForwards)
    stamp.addAnimation_forKey_(anim, "introFade")
    host.addSublayer_(stamp)


def run_gui(edl_path: str, fps_override: int | None, watch: bool, stress: int, loop: bool = False) -> int:
    from AVFoundation import AVPlayer, AVPlayerItem
    from AVKit import AVPlayerView
    from Cocoa import (
        NSApplication,
        NSApplicationActivationPolicyRegular,
        NSBackingStoreBuffered,
        NSMakeRect,
        NSWindow,
    )
    from CoreMedia import CMTimeGetSeconds, kCMTimeZero
    from Foundation import NSDate, NSDefaultRunLoopMode, NSRunLoop

    style = (1 << 0) | (1 << 1) | (1 << 3)  # titled | closable | resizable

    def load():
        clips, fps, music, title = load_edl_file(edl_path)
        return clips, (fps_override or fps), music, title

    def pump(seconds: float) -> None:
        # Manually service the run loop. More robust than scheduled timers under PyObjC,
        # and it keeps prints synchronous so latency lines always appear.
        NSRunLoop.currentRunLoop().runMode_beforeDate_(
            NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(seconds)
        )

    def make_item(clips_, fps_, music_):
        """Composition -> player item, with the music bed's audio mix + a fade-to-black ending."""
        comp = build_composition(clips_, fps=fps_, music=music_)
        item = AVPlayerItem.playerItemWithAsset_(comp)
        mix = build_audio_mix(comp, music_, fps=fps_)
        if mix is not None:
            item.setAudioMix_(mix)
        vc = build_video_composition(comp, fps=fps_, tail_s=_TAIL_FADE_S)
        if vc is not None:
            item.setVideoComposition_(vc)
        return comp, item

    clips, fps, music, title = load()
    player = AVPlayer.playerWithPlayerItem_(make_item(clips, fps, music)[1])

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(150, 150, 1280, 760), style, NSBackingStoreBuffered, False
    )
    win.setTitle_("composerV preview")
    view = AVPlayerView.alloc().initWithFrame_(win.contentView().bounds())
    view.setAutoresizingMask_(18)  # width|height
    view.setPlayer_(player)
    if title:
        _add_date_overlay(view, title)
    win.contentView().addSubview_(view)
    win.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)
    player.play()

    def swap_to(item) -> tuple[float, int]:
        """replaceCurrentItem then pump until readyToPlay; return (swap_ms, status)."""
        t0 = time.perf_counter()
        player.replaceCurrentItemWithPlayerItem_(item)
        player.play()
        deadline = t0 + 3.0
        while item.status() == 0 and time.perf_counter() < deadline:  # 0 = unknown/loading
            pump(0.005)
        return (time.perf_counter() - t0) * 1000, item.status()

    def reload(clips_, fps_, music_, why) -> None:
        cur = player.currentTime()
        t0 = time.perf_counter()
        _comp, item = make_item(clips_, fps_, music_)
        build_ms = (time.perf_counter() - t0) * 1000
        swap_ms, st = swap_to(item)
        try:
            player.seekToTime_(cur)
        except Exception:
            pass
        print(f"{why}: build={build_ms:.1f}ms swap->ready={swap_ms:.1f}ms status={st}", flush=True)

    if stress:
        only = [c for c in clips if c.get("kind") != "gap"]
        swaps: list[float] = []
        for i in range(stress):
            seq = only[:]
            random.shuffle(seq)
            t0 = time.perf_counter()
            _comp, item = make_item(seq, fps, music)
            build_ms = (time.perf_counter() - t0) * 1000
            swap_ms, st = swap_to(item)
            swaps.append(swap_ms)
            print(f"stress {i+1}/{stress}: build={build_ms:.1f}ms swap->ready={swap_ms:.1f}ms status={st}",
                  flush=True)
            pump(0.15)  # let a few frames actually render so the change is visible
        print(
            f"STRESS DONE {len(swaps)}x: swap->ready "
            f"p50={_pct(swaps,50):.1f}ms p95={_pct(swaps,95):.1f}ms max={max(swaps):.1f}ms",
            flush=True,
        )

    last_mtime = os.path.getmtime(edl_path) if watch else None
    msg = "preview window open; Ctrl-C to quit."
    if watch:
        msg += " Edit the EDL to live-reload."
    if loop:
        msg += " Looping (no item swaps): watch the clip->clip cuts for any flash."
    print(msg, flush=True)
    try:
        while True:
            pump(0.05)
            if loop:
                cur = player.currentItem()
                if cur is not None:
                    dur = CMTimeGetSeconds(cur.duration())
                    now = CMTimeGetSeconds(player.currentTime())
                    if dur > 0 and now >= dur - 0.05:
                        player.seekToTime_(kCMTimeZero)
                        player.play()
            if watch:
                try:
                    m = os.path.getmtime(edl_path)
                except OSError:
                    continue
                if m != last_mtime:
                    last_mtime = m
                    cl, fp, mu, _t = load()
                    reload(cl, fp, mu, "reload")
    except KeyboardInterrupt:
        pass
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="composerv-preview", description="Live preview / latency harness")
    ap.add_argument("edl", help="path to an EDL JSON file")
    ap.add_argument("--fps", type=int, default=None, help="override timeline fps")
    ap.add_argument("--check", action="store_true", help="headless: report duration + rebuild latency")
    ap.add_argument("--iterations", type=int, default=20, help="rebuilds to time in --check mode")
    ap.add_argument("--watch", action="store_true", help="GUI: reload when the EDL file changes")
    ap.add_argument("--stress", type=int, default=0, help="GUI: apply N randomized re-edits, time swap->ready")
    ap.add_argument("--loop", action="store_true", help="GUI: loop one composition with no swaps (isolate cut seams)")
    args = ap.parse_args(argv)

    if args.check:
        return check(args.edl, args.fps, args.iterations)
    return run_gui(args.edl, args.fps, args.watch, args.stress, args.loop)


if __name__ == "__main__":
    raise SystemExit(main())
