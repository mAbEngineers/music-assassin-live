#!/usr/bin/env python3
"""Routing dry-run: create the trap sink, find it, destroy it.

Does NOT touch the default sink (safe to run while audio is in use).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio import routing  # noqa: E402


def main():
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


if __name__ == "__main__":
    main()
