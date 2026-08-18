#!/usr/bin/env python3
"""What check() reports, and when (ROADMAP C1/C3). No PipeWire needed.

check() carries every routing decision the app makes once it is running —
re-assert the trap, follow a device change, notice a device vanished, notice
the trap itself vanished — and had no test at all. The states are cheap to
fake and expensive to reproduce for real (the trap-lost case is literally the
2026-08-18 incident), so they are faked here.

The distinction these lock down: 'real_sink_changed' means a human picked a
device and the app should follow AND remember it; 'real_sink_replaced' means
the device they picked went away. Conflating them makes a sleeping Bluetooth
headset silently overwrite a saved output preference.

Run: .venv/bin/python tests/test_routing_events.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio.backends import pipewire as pw  # noqa: E402
from assassin_live.audio.backends.base import SinkInfo  # noqa: E402

FAILURES = []

TRAP = SinkInfo(1, pw.SINK_NAME, "Music Assassin")
HEADPHONES = SinkInfo(2, "bluez_output.headphones", "Headphones")
SPEAKERS = SinkInfo(3, "alsa_output.speakers", "Speakers")


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def advance(self, dt):
        self.t += dt

    def sleep(self, dt):
        self.t += dt


class Graph:
    """A stand-in PipeWire graph: which sinks exist, which is default."""

    def __init__(self, sinks, default):
        self.sinks, self.default = list(sinks), default
        self.set_default_calls = []

    def install(self, clock):
        pw.list_sinks = lambda: list(self.sinks)
        pw.get_default_sink = lambda: self.default
        pw.find_sinks_named = lambda n: [s for s in self.sinks if s.name == n]
        pw.set_default = self._set_default
        pw.time = clock

    def _set_default(self, node_id):
        self.set_default_calls.append(node_id)
        for s in self.sinks:
            if s.id == node_id:
                self.default = s


def backend(graph, clock, real=HEADPHONES):
    graph.install(clock)
    b = pw.PipeWireBackend()
    b.trap, b.real = TRAP, real
    return b


def test_steady_state():
    print("\nnothing happening")
    clock = FakeClock()
    g = Graph([TRAP, HEADPHONES], TRAP)
    b = backend(g, clock)
    check("trap is default and our sink exists -> no event", b.check() is None)
    check("and nothing is re-asserted", g.set_default_calls == [])


def test_user_picks_a_device_in_the_system_menu():
    print("\nsomeone picks Speakers in the system menu")
    clock = FakeClock()
    g = Graph([TRAP, HEADPHONES, SPEAKERS], SPEAKERS)
    b = backend(g, clock)

    first = b.check()
    check("not acted on instantly (a transient must not move the audio)",
          first is None, f"got {first!r}")
    check("but the trap is re-asserted immediately, so we never sit bypassed",
          g.set_default_calls == [TRAP.id], str(g.set_default_calls))

    g.default = SPEAKERS                      # they really did pick it
    clock.advance(pw.PipeWireBackend.RETARGET_DEBOUNCE_S + 0.01)
    second = b.check()
    check("held steady past the debounce -> real_sink_changed",
          second == "real_sink_changed", f"got {second!r}")
    check("and that is the device we now play to", b.real.name == SPEAKERS.name)


def test_debounce_is_short_enough_to_feel_immediate():
    """C3's complaint was a 1.5-2.5 s lag between picking a device and the app
    reacting. The debounce now only has to outlast WirePlumber settling."""
    print("\nthe debounce")
    check("well under the ~1 s that reads as lag",
          pw.PipeWireBackend.RETARGET_DEBOUNCE_S <= 0.5,
          f"{pw.PipeWireBackend.RETARGET_DEBOUNCE_S}s")
    check("but not zero — a transient default must still be rejected",
          pw.PipeWireBackend.RETARGET_DEBOUNCE_S > 0)


def test_flapping_device_is_not_followed():
    print("\na device flapping between two candidates")
    clock = FakeClock()
    g = Graph([TRAP, HEADPHONES, SPEAKERS], SPEAKERS)
    b = backend(g, clock)
    events = []
    for candidate in (SPEAKERS, HEADPHONES, SPEAKERS, HEADPHONES):
        g.default = candidate
        clock.advance(pw.PipeWireBackend.RETARGET_DEBOUNCE_S * 0.4)
        events.append(b.check())
    check("no candidate ever holds long enough to be followed",
          all(e is None for e in events), str(events))


def test_our_device_vanishes():
    print("\nthe device we were using disappears")
    clock = FakeClock()
    g = Graph([TRAP, SPEAKERS], TRAP)           # headphones gone
    b = backend(g, clock, real=HEADPHONES)
    got = b.check()
    check("reported as replaced, NOT as a user choice",
          got == "real_sink_replaced", f"got {got!r}")
    check("and we fell back to what is left", b.real.name == SPEAKERS.name)

    g2 = Graph([TRAP], TRAP)                    # nothing left at all
    b2 = backend(g2, clock, real=HEADPHONES)
    got2 = b2.check()
    check("nothing to fall back to -> real_sink_lost", got2 == "real_sink_lost",
          f"got {got2!r}")


def test_the_trap_itself_vanishes():
    """The 2026-08-18 incident. Previously indistinguishable from 'someone
    stole the default', so the app re-asserted a destroyed node id once a
    second, forever, while reporting itself healthy."""
    print("\nthe trap sink is destroyed under us")
    clock = FakeClock()
    g = Graph([HEADPHONES], HEADPHONES)         # trap gone from the graph
    b = backend(g, clock)
    got = b.check()
    check("reported as trap_lost", got == "trap_lost", f"got {got!r}")
    check("and we do NOT try to re-assert a node that no longer exists",
          g.set_default_calls == [], str(g.set_default_calls))


def main() -> int:
    print("routing event tests (no PipeWire required)")
    saved = (pw.list_sinks, pw.get_default_sink, pw.find_sinks_named,
             pw.set_default, pw.time)
    try:
        test_steady_state()
        test_user_picks_a_device_in_the_system_menu()
        test_debounce_is_short_enough_to_feel_immediate()
        test_flapping_device_is_not_followed()
        test_our_device_vanishes()
        test_the_trap_itself_vanishes()
    finally:
        (pw.list_sinks, pw.get_default_sink, pw.find_sinks_named,
         pw.set_default, pw.time) = saved
    print(f"\n{'FAIL' if FAILURES else 'PASS'}"
          + (f" — {len(FAILURES)}: {', '.join(FAILURES)}" if FAILURES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
