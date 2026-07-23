"""python -m assassin_live [--headless --model NAME] [--recover] [--list-models]"""

import argparse
import time

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
    engine = AudioEngine(proc)
    engine.set_bypass(args.bypass)
    engine.set_midside(args.midside)
    engine.start(routing.monitor_source, real.name)
    print(f"filtering -> {real.name}  (model: {proc.name}, Ctrl-C to stop)")
    try:
        while True:
            time.sleep(1)
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
