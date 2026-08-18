#!/usr/bin/env python3
"""C2 spike: does a sink's volume actually scale what its monitor captures?

C2 rests on a premise: because the trap sink is the system default, the
volume keys scale the *captured* signal, so the system volume silently
changes what the model is fed — which matters a great deal given §2.1, where
this model's output was found to depend on input level by ~36 dB.

That premise is a hypothesis, and it is not obviously true: PipeWire's
monitor ports are not necessarily post-volume. Measuring it costs a minute;
building the mirror-and-pin machinery on a false premise costs a day and
leaves a worse UI behind (the system slider snapping back to 100% forever).

Method: a null sink of our own, a tone played into it, its monitor captured,
RMS compared at volume 1.0 vs 0.5. If the ratio is ~0.5 the premise holds and
C2 is real. If it is ~1.0 the monitor is pre-volume, and C2's rationale — both
the UX half and the measurement-confound half — needs rewriting rather than
implementing.

SAFETY, same as scripts/spike_c1_retarget.py: creates its own null sink,
destroys only the node id it created (never a sweep by name), never touches
the default sink, and plays into a null sink so nothing is audible. It does
change that one sink's volume — its own, which it then removes.

Run: .venv/bin/python scripts/spike_c2_monitor_volume.py
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio.backends import pipewire as pw  # noqa: E402

NAME = "SpikeC2"
RATE, BLOCK, FREQ = 48000, 960, 440.0


def make_sink(name: str, timeout_s: float = 3.0):
    spec = ("{ factory.name=support.null-audio-sink"
            f' node.name={name} node.description="{name}"'
            " media.class=Audio/Sink object.linger=true audio.position=[FL FR] }")
    subprocess.run(["pw-cli", "create-node", "adapter", spec],
                   capture_output=True, timeout=10)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        found = pw.find_sinks_named(name)
        if found:
            return found[0]
        time.sleep(0.1)
    raise RuntimeError(f"{name} did not appear")


def set_volume(node_id: int, vol: float) -> None:
    subprocess.run(["wpctl", "set-volume", str(node_id), f"{vol:.3f}"],
                   capture_output=True, timeout=10)


def read_volume(node_id: int) -> str:
    return pw._run(["wpctl", "get-volume", str(node_id)]).strip()


def main() -> int:
    import sounddevice as sd

    sink = None
    out_s = in_s = None
    captured = []

    try:
        sink = make_sink(NAME)
        print(f"  created {NAME} id={sink.id}")

        os.environ["PULSE_SINK"] = NAME
        os.environ["PULSE_SOURCE"] = f"{NAME}.monitor"
        sd._terminate(); sd._initialize()
        dev = next((i for i, d in enumerate(sd.query_devices())
                    if d["name"] == "pulse"), "default")

        phase = {"t": 0}

        def play(outdata, frames, _t, _s):
            n = np.arange(phase["t"], phase["t"] + frames)
            phase["t"] += frames
            tone = (0.5 * np.sin(2 * np.pi * FREQ * n / RATE)).astype(np.float32)
            outdata[:] = np.stack([tone, tone], axis=1)

        def rec(indata, frames, _t, _s):
            captured.append(indata.copy())

        out_s = sd.OutputStream(device=dev, samplerate=RATE, blocksize=BLOCK,
                                channels=2, dtype="float32", callback=play)
        in_s = sd.InputStream(device=dev, samplerate=RATE, blocksize=BLOCK,
                              channels=2, dtype="float32", callback=rec)
        out_s.start(); in_s.start()
        time.sleep(0.6)
        if not pw.pin_process_streams(os.getpid(), NAME, NAME):
            print("  FAIL: could not pin our streams to the spike sink")
            return 1
        time.sleep(0.8)

        def measure(label, seconds=1.2):
            captured.clear()
            time.sleep(seconds)
            if not captured:
                return None
            x = np.concatenate(captured)
            rms = float(np.sqrt(np.mean(np.square(x))))
            print(f"  {label:<28} rms={rms:.5f}   ({read_volume(sink.id)})")
            return rms

        set_volume(sink.id, 1.0); time.sleep(0.4)
        loud = measure("sink volume 1.0")
        set_volume(sink.id, 0.5); time.sleep(0.4)
        quiet = measure("sink volume 0.5")

        if not loud or not quiet:
            print("  FAIL: captured nothing — monitor not connected?")
            return 1

        ratio = quiet / loud
        print(f"\n  ratio (0.5 vol / 1.0 vol) = {ratio:.3f}")
        print("\n  VERDICT: ", end="")
        if ratio < 0.75:
            print("the monitor IS post-volume — C2's premise holds.")
            print("  The system volume really does scale what the model is fed,")
            print("  so pinning the trap at unity and mirroring onto the real")
            print("  sink is the right fix, and the hardware tier really does")
            print("  have a free variable in it.")
            return 0
        print("the monitor is NOT scaled by the sink's volume.")
        print("  C2's premise is false on this system: the volume keys cannot")
        print("  be changing what the model sees this way. The UX half (the")
        print("  slider not controlling real output) may still stand, but the")
        print("  measurement-confound half — the reason C2 was promoted — does")
        print("  not, and §2.1's swing needs another explanation.")
        return 0
    finally:
        for st in (in_s, out_s):
            if st is not None:
                try:
                    st.stop(); st.close()
                except Exception:  # noqa: BLE001
                    pass
        if sink is not None:
            pw.destroy_node(sink.id)
            print(f"  destroyed {NAME} (id {sink.id})")


if __name__ == "__main__":
    raise SystemExit(main())
