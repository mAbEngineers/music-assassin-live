#!/usr/bin/env python3
"""C1 spike: can a RUNNING playback stream be moved to another device by
writing target.object, instead of being torn down and rebuilt?

ROADMAP C1 turns on this one empirical question. Everything else about C1 is
already written (backend.retarget_playback, AudioEngine.retarget_output, the
UI fallback ladder) and is covered by tests/test_retarget_live.py — but no
test can answer whether WirePlumber honours a target change on a stream that
is *already linked and playing*, because that is a property of the running
system. This script answers it, and measures how long the move takes.

WHY THIS IS SAFE TO RUN, unlike test_routing_dry.py:
  * It creates its own two null sinks, under its own names, and destroys
    ONLY the node ids it created. It never sweeps by name, so it cannot
    delete a sink belonging to a running instance (which is exactly how the
    2026-08-18 incident happened -- see ROADMAP C8).
  * It never changes the default sink.
  * It plays silence, into null sinks. Nothing is audible at any point.
  * It touches no state file and no app process.

It is still a graph mutation, so it is a script you run deliberately, not
part of any suite.

Run: .venv/bin/python scripts/spike_c1_retarget.py
"""

import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio.backends import pipewire as pw  # noqa: E402

SPIKE_A, SPIKE_B = "SpikeC1_A", "SpikeC1_B"
RATE, BLOCK = 48000, 960


def make_null_sink(name: str, timeout_s: float = 3.0) -> pw.SinkInfo:
    spec = (
        "{ factory.name=support.null-audio-sink"
        f' node.name={name} node.description="{name}"'
        " media.class=Audio/Sink object.linger=true audio.position=[FL FR] }"
    )
    subprocess.run(["pw-cli", "create-node", "adapter", spec],
                   capture_output=True, timeout=10)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        found = pw.find_sinks_named(name)
        if found:
            return found[0]
        time.sleep(0.1)
    raise RuntimeError(f"{name} did not appear in the graph")


def playback_peer(pid: int) -> str | None:
    """Which node is our playback stream currently linked into?"""
    objs = pw._pw_dump_all()
    nodes = pw._of_type(objs, ":Node")
    name_by_id = {n["id"]: pw._props(n).get("node.name", "") for n in nodes}
    ours = {o["id"] for o in pw._of_type(objs, ":Client")
            if pw._props(o).get("pipewire.sec.pid") == pid}
    play = {n["id"] for n in nodes
            if pw._props(n).get("client.id") in ours
            and pw._props(n).get("media.class") == "Stream/Output/Audio"}
    peers = {name_by_id.get(pw._props(link).get("link.input.node"))
             for link in pw._of_type(objs, ":Link")
             if pw._props(link).get("link.output.node") in play}
    peers.discard(None)
    peers.discard("")
    return sorted(peers)[0] if peers else None


def wait_for_peer(pid: int, want: str, timeout_s: float = 5.0):
    """Returns (moved, seconds, final_peer)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        peer = playback_peer(pid)
        if peer == want:
            return True, time.monotonic() - t0, peer
        time.sleep(0.05)
    return False, time.monotonic() - t0, playback_peer(pid)


def main() -> int:
    import os

    import sounddevice as sd

    pid = os.getpid()
    created = []
    calls = {"n": 0, "gap_ms": 0.0, "last": None, "status": 0}

    def callback(outdata, frames, _time, status):
        now = time.monotonic()
        if calls["last"] is not None:
            gap = (now - calls["last"]) * 1000.0
            calls["gap_ms"] = max(calls["gap_ms"], gap)
        calls["last"] = now
        calls["n"] += 1
        if status:
            calls["status"] += 1
        outdata[:] = np.zeros((frames, 2), dtype=np.float32)  # silence

    stream = None
    try:
        a = make_null_sink(SPIKE_A); created.append(a)
        b = make_null_sink(SPIKE_B); created.append(b)
        print(f"  created {SPIKE_A} id={a.id}, {SPIKE_B} id={b.id}")

        os.environ["PULSE_SINK"] = SPIKE_A
        sd._terminate(); sd._initialize()
        dev = next((i for i, d in enumerate(sd.query_devices())
                    if d["name"] == "pulse"), "default")
        stream = sd.OutputStream(device=dev, samplerate=RATE, blocksize=BLOCK,
                                 channels=2, dtype="float32", callback=callback)
        stream.start()
        time.sleep(0.5)

        if not pw.pin_process_streams(pid, None, SPIKE_A):
            print("  FAIL: could not pin the stream to A at all")
            return 1
        ok, secs, peer = wait_for_peer(pid, SPIKE_A)
        print(f"  linked to A: {ok} after {secs:.2f}s (peer={peer!r})")
        if not ok:
            print("  FAIL: stream never linked to A; nothing to move")
            return 1

        blocks_before = calls["n"]
        calls["gap_ms"] = 0.0
        print(f"\n  --- moving A -> B on the LIVE stream ---")
        t0 = time.monotonic()
        wrote = pw.pin_process_streams(pid, None, SPIKE_B)
        write_s = time.monotonic() - t0
        moved, move_s, peer = wait_for_peer(pid, SPIKE_B)
        time.sleep(0.3)
        blocks_after = calls["n"]

        print(f"  metadata write returned {wrote} in {write_s:.2f}s")
        print(f"  link moved: {moved} after {move_s:.2f}s (peer now {peer!r})")
        print(f"  stream still active: {stream.active}")
        print(f"  callbacks during the move: {blocks_after - blocks_before} "
              f"(expected ~{int((write_s + move_s + 0.3) * RATE / BLOCK)})")
        print(f"  worst callback gap: {calls['gap_ms']:.1f} ms "
              f"(block period is {1000.0 * BLOCK / RATE:.1f} ms)")
        print(f"  PortAudio status flags seen: {calls['status']}")

        print("\n  VERDICT: ", end="")
        if moved and stream.active and blocks_after > blocks_before:
            print("live retarget WORKS — C1's fast path is real.")
            print("  Read the worst-gap number above: if it is far over one")
            print("  block period, the move is seamless in routing but not in")
            print("  audio, and C1's next rung (ramp wet to 0 across the")
            print("  switch) is still needed.")
            return 0
        print("live retarget did NOT work on this system.")
        print("  Keep AudioEngine.retarget() as the path; C1 falls back to")
        print("  its cheaper rungs (keep processor state across the rebuild,")
        print("  ramp the gain, pre-warm the new stream).")
        return 1
    finally:
        if stream is not None:
            try:
                stream.stop(); stream.close()
            except Exception:  # noqa: BLE001
                pass
        # Only ids we made ourselves. Never a sweep by name.
        for sink in created:
            pw.destroy_node(sink.id)
        print(f"  cleaned up {len(created)} spike sink(s) by id")


if __name__ == "__main__":
    raise SystemExit(main())
