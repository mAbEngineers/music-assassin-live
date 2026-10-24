"""python -m assassin_live [--headless --model NAME] [--recover] [--list-models]"""

import argparse
import sys
import time

from .paths import models_dir
from . import processors


def _report_unsupported(reason: str, gui: bool) -> None:
    """Say why we cannot run, through a channel the user can actually see.

    stderr always, because it is scriptable and shows up in journals. Plus a
    dialog on the GUI path: a --windowed PyInstaller build has no console
    attached, so a printed message there reaches nobody, which is exactly
    how "it just doesn't open" bug reports get written.
    """
    print(reason, file=sys.stderr)
    if not gui:
        return
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("Music Assassin Live", reason)
        root.destroy()
    except Exception:
        pass    # no display or no tk: stderr above is the fallback


def main():
    ap = argparse.ArgumentParser(prog="assassin_live")
    ap.add_argument("--headless", action="store_true",
                    help="no GUI; enable filter until Ctrl-C")
    ap.add_argument("--model", default="dpdfnet_hr")
    ap.add_argument("--bypass", action="store_true",
                    help="route audio but skip processing (plumbing test)")
    ap.add_argument("--midside", action="store_true",
                    help="enable the mid/side stereo pre-filter ahead of the model")
    ap.add_argument("--no-bandlimit", action="store_true",
                    help="disable the ~20 Hz-20 kHz band-limit on the processed output (on by default)")
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--recover", action="store_true",
                    help="clean up stale trap sinks and restore default sink")
    args = ap.parse_args()

    if args.list_models:
        for n in processors.available(models_dir()):
            print(n)
        return

    # Everything below drives a platform audio graph. Check one is actually
    # reachable before touching it, so an unsupported platform or a missing
    # PipeWire produces a sentence instead of a FileNotFoundError traceback
    # on 'pw-dump'. --list-models above needs none of it, which is why the
    # guard sits here rather than at the top of main().
    from .audio.routing import unsupported_reason
    reason = unsupported_reason()
    if reason:
        _report_unsupported(reason, gui=not args.headless and not args.recover)
        raise SystemExit(2)

    if args.recover:
        from .audio.routing import RoutingSession
        RoutingSession.recover_stale()
        print("recovered")
        return

    if not args.headless:
        from .ui.app import main as gui_main
        gui_main()
        return

    from .audio.engine import AudioEngine
    from .audio.routing import RoutingSession

    routing = RoutingSession()
    routing.recover_stale()
    proc = processors.create(args.model, models_dir())
    real = routing.enable()
    if real is None:
        routing.disable()
        raise SystemExit("no hardware sink available")
    engine = AudioEngine(proc, routing)
    engine.set_bypass(args.bypass)
    engine.set_midside(args.midside)
    engine.set_bandlimit(not args.no_bandlimit)
    engine.start(routing.monitor_source, real.name)
    print(f"filtering -> {real.name}  (model: {proc.name}, Ctrl-C to stop)")
    try:
        while True:
            time.sleep(1)
            if not engine.stream_ok:
                # stream died silently (uncaught callback exception or the
                # device vanishing) -- try the same recovery as a real
                # sink change before giving up.
                try:
                    engine.retarget(routing.monitor_source, routing.real.name)
                    print("\naudio stream recovered automatically")
                except Exception as e:  # noqa: BLE001
                    print(f"\naudio stream died, restart failed, stopping: {e}")
                    break
                continue
            event = routing.check()
            if event == "real_sink_changed" and routing.real:
                try:
                    engine.retarget(routing.monitor_source, routing.real.name)
                except Exception as e:  # noqa: BLE001 — don't limp along on a
                    # broken stream; stop cleanly (finally below restores
                    # the sink) rather than leaving state half-broken.
                    print(f"\nretarget failed, stopping: {e}")
                    break
            elif event == "real_sink_lost":
                print("output device lost, waiting...")
            s = engine.stats
            print(f"\r{s.worker_ms_avg:5.1f} ms/block  blocks={s.blocks_in}"
                  f"  fallbacks={s.fallback_blocks}  xruns={s.xruns}   ",
                  end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        routing.disable()
        print("\nrestored")


if __name__ == "__main__":
    main()
