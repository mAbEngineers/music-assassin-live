#!/usr/bin/env python3
"""Live output retarget (ROADMAP C1). No audio hardware, no PipeWire.

The win C1 is after is not "fewer subprocess calls" — it is that an output
device change stops destroying things it never needed to touch. So these
assert on that directly: after a live retarget the processor has not been
reset, the buffers still hold the audio they held, and the stream object is
the same one. A version that "worked" while resetting the model would pass a
returns-True test and still be the bug.

Whether WirePlumber actually honours the metadata write on an already-linked
stream is a property of the running system and cannot be asserted here; that
is what scripts/spike_c1_retarget.py answers. What IS asserted here is that
every way of failing falls back rather than breaking.

Run: .venv/bin/python tests/test_retarget_live.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio.engine import AudioEngine  # noqa: E402
from assassin_live.processors.base import StreamProcessor  # noqa: E402

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


class CountingProc(StreamProcessor):
    name, sample_rate, latency_samples = "counting", 48000, 0

    def __init__(self):
        self.resets = 0

    def reset(self) -> None:
        self.resets += 1

    def feed(self, x):
        return x


class FakeStream:
    def __init__(self, active=True):
        self.active = active


class FakeBackend:
    """Records what the engine asks of it."""

    def __init__(self, result=True, raises=False):
        self.result, self.raises = result, raises
        self.calls = []

    def retarget_playback(self, pid, sink_name):
        self.calls.append((pid, sink_name))
        if self.raises:
            raise RuntimeError("pw-metadata exploded")
        return self.result


class BackendWithoutC1:
    """A backend written before retarget_playback existed."""


def _engine(backend, active=True):
    proc = CountingProc()
    eng = AudioEngine(proc, backend)
    eng._stream = FakeStream(active)
    # give the FIFOs recognisable contents so we can prove they survive
    eng._out = np.ones((480, 2), dtype=np.float32)
    eng._dry_out = np.full((480, 2), 2.0, dtype=np.float32)
    proc.resets = 0            # ignore the reset _build_runtime does at init
    return eng, proc


def test_live_move_preserves_everything():
    print("\nthe live path")
    backend = FakeBackend(result=True)
    eng, proc = _engine(backend)
    stream_before = eng._stream

    moved = eng.retarget_output("bluez_output.new_headphones")

    check("reports the move succeeded", moved is True)
    check("asked the backend for exactly that sink",
          len(backend.calls) == 1 and backend.calls[0][1] == "bluez_output.new_headphones",
          str(backend.calls))
    check("processor was NOT reset — the model keeps its state",
          proc.resets == 0, f"resets={proc.resets}")
    check("the stream was not reopened", eng._stream is stream_before)
    check("wet buffer survived", len(eng._out) == 480 and bool(np.all(eng._out == 1.0)))
    check("dry buffer survived", len(eng._dry_out) == 480 and bool(np.all(eng._dry_out == 2.0)))


def test_every_failure_falls_back_rather_than_breaking():
    print("\nfallback, not failure")
    for label, backend, active in (
            ("backend says no", FakeBackend(result=False), True),
            ("backend raises", FakeBackend(raises=True), True),
            ("backend predates C1", BackendWithoutC1(), True),
            ("stream is dead", FakeBackend(result=True), False),
            ("no backend at all", None, True)):
        eng, proc = _engine(backend, active)
        got = eng.retarget_output("some_sink")
        check(f"{label} -> False (caller falls back)", got is False, f"got {got!r}")
        check(f"{label} -> nothing was reset", proc.resets == 0)


def test_dead_stream_is_not_touched():
    """A dead stream needs the full rebuild, and asking the graph to move a
    node belonging to a stream that no longer plays would either fail slowly
    (3 s of polling) or succeed meaninglessly."""
    print("\ndead stream short-circuits")
    backend = FakeBackend(result=True)
    eng, _ = _engine(backend, active=False)
    eng.retarget_output("some_sink")
    check("backend was never called", backend.calls == [], str(backend.calls))


def main() -> int:
    print("live retarget tests (no audio hardware, no PipeWire)")
    test_live_move_preserves_everything()
    test_every_failure_falls_back_rather_than_breaking()
    test_dead_stream_is_not_touched()
    print(f"\n{'FAIL' if FAILURES else 'PASS'}"
          + (f" — {len(FAILURES)}: {', '.join(FAILURES)}" if FAILURES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
