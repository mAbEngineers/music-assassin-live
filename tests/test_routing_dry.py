#!/usr/bin/env python3
"""Routing dry-run: create the trap sink, find it, destroy it.

NOT hardware-free, despite sitting among tests that are. It mutates the live
PipeWire graph, and its first action DESTROYS every sink named MusicAssassin
-- including one a running instance is currently filtering through.

The previous version of this docstring said "does NOT touch the default sink
(safe to run while audio is in use)". The first clause was true and the
parenthesis did not follow from it: on 2026-08-18 this test was run against a
live instance, deleted its trap sink as "stale", and PipeWire re-attached the
orphaned capture stream to the real output sink's monitor -- which the app
also plays into. The result was an audio feedback loop that ran until the app
was killed. See ROADMAP C8, and test_capture_diagnosis.py for the detector
that incident produced.

Hence the guard below. Set ALLOW_LIVE=1 to override it, which is only ever
right when you know the instances it found are dead or irrelevant.

Run: .venv/bin/python tests/test_routing_dry.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio import routing  # noqa: E402


def live_instances() -> list[tuple[int, str]]:
    """Running Music Assassin processes, excluding this one.

    Matched on argv shape rather than a substring of the whole command line:
    this repository's own directory is named music-assassin-live, so a plain
    substring test matches every shell, editor and test runner with the repo
    path in its arguments, and a guard that fires constantly is a guard that
    gets deleted.
    """
    found, me = [], os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            argv = entry.joinpath("cmdline").read_bytes().decode(
                errors="replace").split("\0")
        except OSError:
            continue                      # process exited, or not ours to read
        argv = [a for a in argv if a]
        if not argv:
            continue
        arg0 = os.path.basename(argv[0])
        is_app = (
            arg0 == "music-assassin-live"
            or ("-m" in argv and "assassin_live" in argv)
            or any(a.endswith("run_assassin_live.py") for a in argv[1:])
        )
        if is_app:
            found.append((int(entry.name), " ".join(argv)))
    return found


def main():
    live = live_instances()
    if live and os.environ.get("ALLOW_LIVE") != "1":
        print("REFUSED: Music Assassin appears to be running:")
        for pid, cmd in live:
            print(f"    pid {pid}: {cmd[:100]}")
        print("\n  This test destroys every sink named "
              f"{routing.SINK_NAME!r}, including the one that instance is")
        print("  filtering through, which leaves it capturing its own output"
              " (ROADMAP C8).")
        print("  Close the app first, or set ALLOW_LIVE=1 if you are certain.")
        return 1

    stale = routing.find_sinks_named(routing.SINK_NAME)
    for s in stale:
        print(f"  cleaning stale trap sink {s.id}")
        routing.destroy_node(s.id)

    sink = routing.create_trap_sink()
    print(f"  created trap sink id={sink.id} name={sink.name!r} "
          f"desc={sink.description!r}")
    assert sink.name == routing.SINK_NAME
    assert sink.description == routing.SINK_DESC, "node.description not applied"

    routing.destroy_node(sink.id)
    left = routing.find_sinks_named(routing.SINK_NAME)
    assert not left, f"trap sink still present after destroy: {left}"
    print("  destroyed cleanly")

    print(f"  current default sink: {routing.get_default_sink()}")
    print(f"  hardware sinks visible: "
          f"{[s.name for s in routing.list_sinks()]}")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
