#!/usr/bin/env python3
"""Volume mirroring (ROADMAP C2). No PipeWire, no wpctl.

C2 was promoted on the belief that the trap sink being the system default
meant the volume keys were scaling the signal fed to the model — a
measurement confound, given §2.1 found this model's output depends on input
level by ~36 dB. **That belief is false**, measured by
scripts/spike_c2_monitor_volume.py: a tone captured from a sink's monitor at
volume 1.0 and at 0.5 came back at identical RMS, ratio 1.000. Monitors are
pre-volume.

What is left is still a real bug, just a different one: the keys act on the
trap, whose output goes nowhere, so they do nothing at all. And the fix is
better than the one C2 specified — because the trap's volume is harmless, it
can be left where the user put it, so the system slider keeps showing the
level they chose instead of being pinned to 100% forever.

Run: .venv/bin/python tests/test_volume_mirror.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio.backends import pipewire as pw  # noqa: E402
from assassin_live.audio.backends.base import SinkInfo  # noqa: E402

FAILURES = []

TRAP = SinkInfo(1, pw.SINK_NAME, "Music Assassin")
REAL = SinkInfo(2, "bluez_output.headphones", "Headphones")


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


class Mixer:
    """A stand-in for wpctl: per-node (volume, muted), and a log of writes."""

    def __init__(self, levels=None):
        self.levels = {TRAP.id: (1.0, False), REAL.id: (1.0, False)}
        self.levels.update(levels or {})
        self.writes = []

    def install(self):
        pw.get_volume = lambda nid: self.levels.get(nid)
        pw.set_volume = self._set

    def _set(self, nid, vol, muted):
        self.writes.append((nid, round(vol, 4), muted))
        self.levels[nid] = (vol, muted)


def backend(mixer):
    mixer.install()
    b = pw.PipeWireBackend()
    b.trap, b.real = TRAP, REAL
    return b


def test_adopt_does_not_change_how_loud_anything_is():
    print("\nenabling the filter")
    m = Mixer({REAL.id: (0.42, False)})
    b = backend(m)
    b.adopt_volume()
    check("the trap is seeded from the real device's level",
          m.levels[TRAP.id] == (0.42, False), str(m.levels[TRAP.id]))
    check("so the slider does not jump when the filter goes on",
          m.writes == [(TRAP.id, 0.42, False)], str(m.writes))

    moved = b.sync_volume()
    check("and nothing is mirrored back immediately", moved is None, str(moved))


def test_a_volume_key_reaches_the_real_device():
    print("\nturning the volume down")
    m = Mixer({REAL.id: (0.60, False)})
    b = backend(m)
    b.adopt_volume()
    m.writes.clear()

    m.levels[TRAP.id] = (0.45, False)          # the user pressed volume-down
    moved = b.sync_volume()
    check("the change is detected", moved == (0.45, False), str(moved))
    check("and applied to the real device", m.levels[REAL.id] == (0.45, False),
          str(m.levels[REAL.id]))
    check("the trap keeps the level the user chose — the slider stays honest",
          m.levels[TRAP.id] == (0.45, False), str(m.levels[TRAP.id]))
    check("only the real device was written to",
          [w[0] for w in m.writes] == [REAL.id], str(m.writes))


def test_mute_is_mirrored():
    print("\nmute")
    m = Mixer()
    b = backend(m)
    b.adopt_volume()
    m.levels[TRAP.id] = (1.0, True)
    b.sync_volume()
    check("mute reaches the real device", m.levels[REAL.id] == (1.0, True),
          str(m.levels[REAL.id]))


def test_steady_state_does_not_fight_the_user():
    """If the mirror wrote every tick it would stamp on anyone adjusting the
    real device directly in a mixer, once a second, unfixably."""
    print("\nnothing happening")
    m = Mixer()
    b = backend(m)
    b.adopt_volume()
    m.writes.clear()
    for _ in range(5):
        b.sync_volume()
    check("no writes at all while the trap is untouched", m.writes == [],
          str(m.writes))

    m.levels[REAL.id] = (0.2, False)      # user drags the real device down
    for _ in range(5):
        b.sync_volume()
    check("a direct change to the real device is left alone",
          m.levels[REAL.id] == (0.2, False), str(m.levels[REAL.id]))


def test_no_session_no_writes():
    print("\nnot enabled")
    m = Mixer()
    m.install()
    b = pw.PipeWireBackend()          # trap and real are None
    check("sync_volume is a no-op", b.sync_volume() is None)
    b.adopt_volume()
    check("adopt_volume is a no-op", m.writes == [], str(m.writes))


def main() -> int:
    print("volume mirror tests (no PipeWire, no wpctl)")
    saved = (pw.get_volume, pw.set_volume)
    try:
        test_adopt_does_not_change_how_loud_anything_is()
        test_a_volume_key_reaches_the_real_device()
        test_mute_is_mirrored()
        test_steady_state_does_not_fight_the_user()
        test_no_session_no_writes()
    finally:
        pw.get_volume, pw.set_volume = saved
    print(f"\n{'FAIL' if FAILURES else 'PASS'}"
          + (f" — {len(FAILURES)}: {', '.join(FAILURES)}" if FAILURES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
