#!/usr/bin/env python3
"""What is the app actually capturing? (ROADMAP C8). No audio graph needed.

diagnose_capture() is a pure function over one pw-dump snapshot precisely so
this can exist: the states it detects are ones you otherwise have to *cause*
to test, and causing them means breaking the audio on a live machine. That is
how they were found in the first place, on 2026-08-18 — these fixtures are
modelled on the graph that incident actually produced (node ids and prop names
copied from a real pw-dump, not invented).

Run: .venv/bin/python tests/test_capture_diagnosis.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio.backends.pipewire import diagnose_capture  # noqa: E402

TRAP = "MusicAssassin"
REAL = "bluez_output.37_22_C9_82_4F_52.1"
OURS = 2259217          # our pid
THEIRS = 2122           # some other client's

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


# -- fixture builders, matching real pw-dump shapes ---------------------------

def node(nid: int, name: str, media_class: str | None = None,
         client_id: int | None = None) -> dict:
    props = {"node.name": name}
    if media_class:
        props["media.class"] = media_class
    if client_id is not None:
        props["client.id"] = client_id
    return {"id": nid, "type": "PipeWire:Interface:Node", "info": {"props": props}}


def client(cid: int, pid: int) -> dict:
    return {"id": cid, "type": "PipeWire:Interface:Client",
            "info": {"props": {"pipewire.sec.pid": pid}}}


def link(out_node: int, in_node: int) -> dict:
    return {"id": 900 + out_node, "type": "PipeWire:Interface:Link",
            "info": {"props": {"link.output.node": out_node,
                               "link.input.node": in_node}}}


CLIENT_US, CLIENT_THEM = 40, 41
CAP, PLAY = 218, 121          # our two stream nodes, as seen in the incident
TRAP_NODE, REAL_NODE, MIC = 107, 69, 55


def graph(*, trap=True, capture_from=None, extra=()):
    """A graph with our capture stream linked to `capture_from` (a node id)."""
    objs = [client(CLIENT_US, OURS), client(CLIENT_THEM, THEIRS),
            node(REAL_NODE, REAL, "Audio/Sink"),
            node(MIC, "alsa_input.builtin_mic", "Audio/Source"),
            node(CAP, "ALSA plug-in [music-assassin-live]",
                 "Stream/Input/Audio", CLIENT_US),
            node(PLAY, "ALSA plug-in [music-assassin-live]",
                 "Stream/Output/Audio", CLIENT_US)]
    if trap:
        objs.append(node(TRAP_NODE, TRAP, "Audio/Sink"))
    if capture_from is not None:
        objs.append(link(capture_from, CAP))
    objs.append(link(PLAY, REAL_NODE))        # we always play to the real sink
    objs.extend(extra)
    return objs


# -- the states ---------------------------------------------------------------

def test_healthy():
    print("\nwired as intended")
    check("capture on the trap's monitor is not a fault",
          diagnose_capture(graph(capture_from=TRAP_NODE), OURS, TRAP, REAL) is None)


def test_the_incident():
    """2026-08-18: the trap sink was destroyed under a running instance and
    the capture stream was re-attached to the real sink's monitor — the sink
    we play into. Both halves are checked, because they arrive together but
    are separately detectable and need different responses."""
    print("\nthe 2026-08-18 incident")
    gone = diagnose_capture(graph(trap=False, capture_from=REAL_NODE), OURS, TRAP, REAL)
    check("trap sink gone is reported as trap_lost, not as a loop",
          gone == "trap_lost", f"got {gone!r}")
    loop = diagnose_capture(graph(capture_from=REAL_NODE), OURS, TRAP, REAL)
    check("capturing the monitor of the sink we play into is a feedback loop",
          loop == "feedback_loop", f"got {loop!r}")


def test_hijacked_but_not_looping():
    print("\nwrong source, no loop")
    got = diagnose_capture(graph(capture_from=MIC), OURS, TRAP, REAL)
    check("capture on an unrelated device is hijacked, not a loop",
          got == "capture_hijacked", f"got {got!r}")


def test_nothing_to_judge_yet():
    print("\nstates that are not faults")
    no_link = diagnose_capture(graph(capture_from=None), OURS, TRAP, REAL)
    check("no links yet is not a fault", no_link is None, f"got {no_link!r}")

    no_stream = [o for o in graph(capture_from=TRAP_NODE)
                 if o.get("id") != CAP]
    check("no capture stream open yet is not a fault",
          diagnose_capture(no_stream, OURS, TRAP, REAL) is None)

    # real sink not yet known (routing.real is None): a loop cannot be
    # identified, but a wrong source still can.
    unknown = diagnose_capture(graph(capture_from=REAL_NODE), OURS, TRAP, None)
    check("unknown playback device downgrades loop to hijack, not to silence",
          unknown == "capture_hijacked", f"got {unknown!r}")


def test_other_processes_are_not_ours():
    """Another app capturing the real sink's monitor is completely normal —
    a recorder, a meter, a screen-share. Judging its links as ours would fire
    constantly on a busy machine, and a check that cries wolf gets removed."""
    print("\nother clients are ignored")
    theirs = node(300, "OBS", "Stream/Input/Audio", CLIENT_THEM)
    objs = graph(capture_from=TRAP_NODE, extra=(theirs, link(REAL_NODE, 300)))
    check("another client's capture of our output sink is not our problem",
          diagnose_capture(objs, OURS, TRAP, REAL) is None)


def main() -> int:
    print("capture diagnosis tests (no audio graph required)")
    test_healthy()
    test_the_incident()
    test_hijacked_but_not_looping()
    test_nothing_to_judge_yet()
    test_other_processes_are_not_ours()
    print(f"\n{'FAIL' if FAILURES else 'PASS'}"
          + (f" — {len(FAILURES)}: {', '.join(FAILURES)}" if FAILURES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
