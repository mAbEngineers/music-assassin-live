"""python -m assassin_live [--headless --model NAME] [--recover] [--list-models]"""

import argparse
import os
import time

# Match the GUI's cadence: the capture check costs a pw-dump and the states
# it catches are structural, so once every few seconds is enough.
CAPTURE_CHECK_TICKS = 5

from .paths import models_dir
from . import processors


def main():
    ap = argparse.ArgumentParser(prog="assassin_live")
    ap.add_argument("--headless", action="store_true",
                    help="no GUI; enable filter until Ctrl-C")
    ap.add_argument("--model", default="dpdfnet_hr")
    ap.add_argument("--bypass", action="store_true",
                    help="route audio but skip processing (plumbing test)")
    ap.add_argument("--midside", action="store_true",
                    help="enable the mid/side stereo pre-filter ahead of the model")
    ap.add_argument("--stereo", action="store_true",
                    help="rebuild the stereo image around the mono model "
                         "(ROADMAP B3); off by default, same as the GUI")
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
    engine.set_stereo(args.stereo)
    engine.start(routing.monitor_source, real.name)
    routing.adopt_volume()
    print(f"filtering -> {real.name}  (model: {proc.name}, Ctrl-C to stop)")
    ticks = 0
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
            # Keep the volume keys working (C2) — they act on the trap,
            # whose output goes nowhere, so without this they do nothing.
            try:
                routing.sync_volume()
            except Exception:  # noqa: BLE001 — a volume mirror that cannot
                pass          # run is a wart, not a reason to stop audio.

            event = routing.check()
            # Both device events are followed. real_sink_replaced was split
            # out from real_sink_changed for the GUI (a vanished device is
            # not a user's choice and must not overwrite their saved one) —
            # this loop has no saved preference to protect, but it does have
            # to follow both, and only handling the old name would leave
            # headless silently not retargeting when a device disappears.
            if event in ("real_sink_changed", "real_sink_replaced") and routing.real:
                try:
                    if not engine.retarget_output(routing.real.name):
                        engine.retarget(routing.monitor_source, routing.real.name)
                    print(f"\noutput -> {routing.real.name}")
                except Exception as e:  # noqa: BLE001 — don't limp along on a
                    # broken stream; stop cleanly (finally below restores
                    # the sink) rather than leaving state half-broken.
                    print(f"\nretarget failed, stopping: {e}")
                    break
            elif event == "real_sink_lost":
                print("output device lost, waiting...")
            elif event == "trap_lost":
                print("\nour audio device disappeared — stopping")
                break

            # Same protection the GUI gets (C8): a capture stream can be
            # alive, healthy and wired to the wrong thing, and a feedback
            # loop only gets worse while you decide what to do about it.
            ticks += 1
            if ticks % CAPTURE_CHECK_TICKS == 0:
                try:
                    state = routing.diagnose_capture(os.getpid())
                except Exception:  # noqa: BLE001
                    state = None
                if state == "feedback_loop":
                    print("\nfeedback loop detected (capturing our own "
                          "output) — stopping")
                    break
                if state in ("trap_lost", "capture_hijacked"):
                    print(f"\ncapture is wrong ({state}) — stopping")
                    break
            s = engine.stats
            print(f"\r{engine.latency_ms:5.1f} ms latency  "
                  f"{s.worker_ms_avg:5.1f} ms/block  blocks={s.blocks_in}"
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
