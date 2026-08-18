"""PipeWire trap-sink routing backend (Linux).

Volume-booster behavior: on enable we create a null "trap" sink, make it the
default so WirePlumber migrates every default-following app stream into it,
then the engine captures the trap's monitor and plays the processed result to
the real hardware sink. Disable restores everything.

Uses only pw-cli / wpctl / pw-dump — no pactl (not installed on target dev
machine, PipeWire 1.0.5). Crash recovery: the previous default sink is
persisted to a state file *before* the swap; recover_stale() runs at startup.

Implements the RoutingBackend seam (backends/base.py); routing.py re-exports
PipeWireBackend as RoutingSession, the name callers actually import.
"""

import json
import os
import re
import subprocess
import time

from ...paths import ROUTING_STATE
from .base import SinkInfo

SINK_NAME = "MusicAssassin"
SINK_DESC = "Music Assassin"


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout


def _pw_dump_all() -> list[dict]:
    """Every object in one graph snapshot.

    Callers that need more than one object type (nodes *and* links *and*
    clients, as the capture diagnosis does) must share a single snapshot:
    three separate pw-dump calls are three different moments, and a graph
    that changes between them yields link endpoints referring to node ids
    that are not in the node list — which reads as "capture is connected to
    nothing" rather than as the race it is.
    """
    # pw-dump emits one JSON array per graph snapshot; if the graph changes
    # mid-dump it appends further arrays, so parse them all.
    try:
        text = _run(["pw-dump"])
    except subprocess.SubprocessError:
        return []
    objs, dec, pos = [], json.JSONDecoder(), 0
    while pos < len(text):
        try:
            chunk, end = dec.raw_decode(text, pos)
        except json.JSONDecodeError:
            break
        objs.extend(chunk)
        pos = end
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
    return objs


def _pw_dump(type_suffix: str) -> list[dict]:
    return [o for o in _pw_dump_all() if o.get("type", "").endswith(type_suffix)]


def _props(obj: dict) -> dict:
    return obj.get("info", {}).get("props", {}) or {}


def _of_type(objs: list[dict], suffix: str) -> list[dict]:
    return [o for o in objs if o.get("type", "").endswith(suffix)]


def _owner_pid(props: dict):
    """The pid of the application a client/node belongs to.

    NOT `pipewire.sec.pid`, which is the pid of whatever opened the socket.
    Anything arriving through pipewire-pulse (`client.api ==
    'pipewire-pulse'`, which is how PortAudio's "pulse" device and the ALSA
    plugin both connect) is proxied by the pipewire-pulse daemon, so every
    such client reports the *daemon's* pid — measured on this machine:
    Firefox, GNOME's volume control, our own streams and a dozen others all
    reporting the same 2122. Matching on it finds nothing, forever, silently.

    `application.process.id` is the application's own pid and is carried on
    both the client and the node. `pipewire.sec.pid` stays as the fallback
    for native protocol clients, where it is genuine.
    """
    pid = props.get("application.process.id")
    return pid if pid is not None else props.get("pipewire.sec.pid")


def our_stream_nodes(objs: list[dict], pid: int) -> dict[str, set]:
    """This process's own stream nodes, keyed by media.class.

    Checks the node's own props first — `application.process.id` is on the
    node, so the client indirection is not even needed — and still accepts
    nodes reached via a client that identifies as ours, so a platform that
    only labels the client keeps working.
    """
    ours = {o["id"] for o in _of_type(objs, ":Client")
            if _owner_pid(_props(o)) == pid}
    found: dict[str, set] = {"Stream/Input/Audio": set(),
                             "Stream/Output/Audio": set()}
    for node in _of_type(objs, ":Node"):
        props = _props(node)
        cls = props.get("media.class")
        if cls not in found:
            continue
        if _owner_pid(props) == pid or props.get("client.id") in ours:
            found[cls].add(node["id"])
    return found


def diagnose_capture(objs: list[dict], pid: int, trap_name: str,
                     playback_name: str | None) -> str | None:
    """What is this process's capture stream actually connected to?

    Pure function over one pw-dump snapshot so it is testable without a
    graph. Returns None when the capture is wired as intended (or when
    there is nothing to judge yet), else one of:

      'trap_lost'         the trap sink is gone from the graph entirely.
                          Everything downstream follows from this, so it is
                          reported first and separately.
      'feedback_loop'     capture is linked to the monitor of the very sink
                          we play into. Output feeds input feeds output; it
                          is audible immediately and gets worse, never
                          better. Unconditionally wrong for this app.
      'capture_hijacked'  capture is linked to something that is neither the
                          trap nor the playback sink — the filter is
                          processing audio nobody asked it to process.

    WHY THIS EXISTS (2026-08-18): the trap sink was destroyed under a
    running instance. WirePlumber did the reasonable thing and re-attached
    the orphaned capture stream to the current default sink's monitor --
    which is the sink we play into -- and the app ran as a feedback loop
    until it was killed. `stream_ok` stayed True throughout, correctly: the
    stream was alive and healthy, it was simply connected to the wrong
    thing. Liveness was never the property worth checking.
    """
    nodes = _of_type(objs, ":Node")
    name_by_id = {n["id"]: _props(n).get("node.name", "") for n in nodes}
    if trap_name not in name_by_id.values():
        return "trap_lost"

    capture_ids = our_stream_nodes(objs, pid)["Stream/Input/Audio"]
    if not capture_ids:
        return None      # stream not open yet — nothing to judge

    peers = {name_by_id.get(_props(link).get("link.output.node"))
             for link in _of_type(objs, ":Link")
             if _props(link).get("link.input.node") in capture_ids}
    peers.discard(None)
    peers.discard("")
    if not peers or peers == {trap_name}:
        return None      # not linked yet, or linked exactly as intended

    # Checked before the generic case: a loop is the one that damages the
    # user's ears rather than merely producing wrong audio.
    if playback_name and playback_name in peers:
        return "feedback_loop"
    return "capture_hijacked"


def _pw_dump_nodes() -> list[dict]:
    return _pw_dump(":Node")


def list_sinks() -> list[SinkInfo]:
    sinks = []
    for node in _pw_dump_nodes():
        props = node.get("info", {}).get("props", {})
        if props.get("media.class") == "Audio/Sink":
            sinks.append(SinkInfo(node["id"], props.get("node.name", ""),
                                  props.get("node.description", "")))
    return sinks


def get_default_sink() -> SinkInfo | None:
    out = _run(["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"])
    m_id = re.search(r"^id (\d+)", out)
    m_name = re.search(r'node\.name = "([^"]+)"', out)
    m_desc = re.search(r'node\.description = "([^"]+)"', out)
    if not (m_id and m_name):
        return None
    return SinkInfo(int(m_id.group(1)), m_name.group(1),
                    m_desc.group(1) if m_desc else "")


def find_sinks_named(name: str) -> list[SinkInfo]:
    return [s for s in list_sinks() if s.name == name]


def set_default(node_id: int) -> None:
    subprocess.run(["wpctl", "set-default", str(node_id)], check=True, timeout=10)


def destroy_node(node_id: int) -> None:
    subprocess.run(["pw-cli", "destroy", str(node_id)],
                   capture_output=True, timeout=10)


def create_trap_sink(timeout_s: float = 3.0) -> SinkInfo:
    spec = (
        "{ factory.name=support.null-audio-sink"
        f' node.name={SINK_NAME} node.description="{SINK_DESC}"'
        " media.class=Audio/Sink object.linger=true audio.position=[FL FR] }"
    )
    subprocess.run(["pw-cli", "create-node", "adapter", spec],
                   capture_output=True, timeout=10)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        found = find_sinks_named(SINK_NAME)
        if found:
            return found[0]
        time.sleep(0.1)
    raise RuntimeError("trap sink did not appear in the PipeWire graph")


def pin_process_streams(pid: int, capture_sink: str | None, playback_sink: str,
                        timeout_s: float = 3.0) -> bool:
    """Point this process's audio streams at explicit sinks.

    capture_sink=None targets the playback stream only and leaves the
    capture stream's target untouched. That is what an output-device change
    needs (C1): the capture side and the model are unaffected by where the
    result is played, so re-asserting the capture target would be at best a
    no-op and at worst an unnecessary relink on the one path that must not
    be interrupted.

    PortAudio's "pulse" device usually resolves to the PipeWire ALSA plugin
    (pipewire-alsa, the Ubuntu 24.04 default), which ignores
    PULSE_SOURCE/PULSE_SINK — freshly opened streams then follow WirePlumber
    defaults: playback lands on the default sink (the trap — a feedback
    loop) and capture on whatever default source exists. Fix: find our own
    stream nodes (via the client object carrying our pid) and set
    target.object metadata. WirePlumber moves the streams; a capture stream
    targeted at a sink is linked to that sink's monitor ports.

    capture_sink/playback_sink are sink node names; the capture stream is
    attached to capture_sink's monitor.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        # One snapshot: nodes and clients read separately are two different
        # moments, and a stream that appears between them is invisible.
        objs = _pw_dump_all()
        wanted = {playback_sink} | ({capture_sink} if capture_sink else set())
        serials = {}
        for node in _of_type(objs, ":Node"):
            props = _props(node)
            if props.get("node.name") in wanted:
                serials[props["node.name"]] = props.get("object.serial")
        mine = our_stream_nodes(objs, pid)
        cap_node = next(iter(mine["Stream/Input/Audio"]), None)
        play_node = next(iter(mine["Stream/Output/Audio"]), None)
        targets = [(play_node, playback_sink)]
        if capture_sink is not None:
            targets.append((cap_node, capture_sink))
        if all(node_id and serials.get(sink) is not None
               for node_id, sink in targets):
            for node_id, sink in targets:
                subprocess.run(
                    ["pw-metadata", str(node_id), "target.object",
                     str(serials[sink])],
                    capture_output=True, timeout=10)
            return True
        time.sleep(0.1)
    return False


class PipeWireBackend:
    """enable() -> filter is inserted; disable() -> system restored.

    check() must be called periodically (~1 s) while enabled; it re-asserts
    the default sink if something (e.g. a reconnecting Bluetooth headset)
    stole it, and reports when the real output sink changed or vanished.

    Satisfies RoutingBackend (backends/base.py) structurally.
    """

    # A candidate replacement sink must be observed as the system default
    # for this long, continuously, before we act on it.
    #
    # Was 1.5 s, for a reason that stopped being true when C1 landed: acting
    # meant a full engine restart, and each restart was an audible glitch,
    # so a flapping device (BT reconnect loop, a race with WirePlumber's own
    # default-sink assignment) had to be waited out. A retarget now costs a
    # metadata write — measured at 0.01–0.04 s with ~1.5 ms of callback
    # jitter and no xruns (ROADMAP C1) — so thrashing is close to free while
    # the wait is not: 1.5 s is precisely the lag that makes picking a
    # device in the system menu feel broken (ROADMAP C3).
    #
    # It does not go to zero, because rejecting a transient is still worth
    # something — WirePlumber briefly assigns a default of its own while
    # devices settle, and following that would move the audio somewhere the
    # user never chose. This is now sized to outlast that race and nothing
    # more.
    RETARGET_DEBOUNCE_S = 0.25

    def __init__(self):
        self.trap: SinkInfo | None = None
        self.real: SinkInfo | None = None
        self.preferred_name: str | None = None  # user-picked output sink, if any
        self._pending: SinkInfo | None = None
        self._pending_since: float = 0.0

    def _pick_real(self, fallback: SinkInfo | None) -> SinkInfo | None:
        if self.preferred_name:
            for s in list_sinks():
                if s.name == self.preferred_name:
                    return s
        return fallback

    # -- crash recovery ----------------------------------------------------
    @staticmethod
    def recover_stale() -> None:
        for s in find_sinks_named(SINK_NAME):
            destroy_node(s.id)
        if ROUTING_STATE.is_file():
            try:
                prev = json.loads(ROUTING_STATE.read_text()).get("previous_default")
                if prev:
                    for s in list_sinks():
                        if s.name == prev:
                            set_default(s.id)
                            break
            finally:
                ROUTING_STATE.unlink(missing_ok=True)

    # -- lifecycle ----------------------------------------------------------
    def enable(self) -> SinkInfo | None:
        """Insert the trap sink. Returns the real output sink (None if no
        hardware sink is currently available — engine should wait)."""
        self.recover_stale()
        prev = get_default_sink()
        if prev and prev.name == SINK_NAME:
            prev = None
        ROUTING_STATE.write_text(json.dumps(
            {"previous_default": prev.name if prev else None, "ts": time.time()}))
        self.trap = create_trap_sink()
        self.real = prev or next(
            (s for s in list_sinks() if s.name != SINK_NAME), None)
        self._pending = None
        self._pending_since = 0.0
        set_default(self.trap.id)
        return self.real

    def disable(self) -> None:
        if self.real:
            for s in list_sinks():
                if s.name == self.real.name:
                    set_default(s.id)
                    break
        if self.trap:
            destroy_node(self.trap.id)
        self.trap = self.real = None
        ROUTING_STATE.unlink(missing_ok=True)

    # -- supervision ----------------------------------------------------------
    def check(self) -> str | None:
        """Returns None (all good), 'real_sink_changed', 'real_sink_lost',
        or 'trap_lost'."""
        if not self.trap:
            return None
        default = get_default_sink()
        if default is None or default.name != SINK_NAME:
            # The trap being *gone* and the default merely being *stolen*
            # are indistinguishable from the default sink alone, and they
            # need opposite responses: stop, vs. re-assert. Tell them apart
            # before acting -- set_default() on a destroyed node id fails
            # silently and would be retried every second forever while the
            # app reported itself healthy. Costs nothing in the common case
            # because a live trap that is still the default never reaches
            # this branch.
            if not find_sinks_named(SINK_NAME):
                return "trap_lost"
            # Something (BT reconnect, a system output picker, WirePlumber's
            # own default-sink logic) stole the default — always re-assert
            # the trap immediately so the filter never sits bypassed, but
            # only report a real device change (and trigger an engine
            # restart) once the candidate has held steady for a bit.
            if default is None:
                set_default(self.trap.id)
                return None
            if self._pending is None or default.name != self._pending.name:
                self._pending = default
                self._pending_since = time.monotonic()
            set_default(self.trap.id)
            stable = time.monotonic() - self._pending_since >= self.RETARGET_DEBOUNCE_S
            if stable and (self.real is None or default.name != self.real.name):
                self.real = default
                return "real_sink_changed"
            return None
        # default is still us; make sure our output device still exists
        self._pending = None
        if self.real and not any(s.name == self.real.name for s in list_sinks()):
            # Distinct from 'real_sink_changed' on purpose. That one means a
            # human picked a device and we should follow and remember it;
            # this one means the device they picked went away and we found
            # something else. Conflating them makes a sleeping Bluetooth
            # headset silently overwrite the user's saved output choice with
            # whatever happened to be left.
            self.real = next(
                (s for s in list_sinks() if s.name != SINK_NAME), None)
            return "real_sink_replaced" if self.real else "real_sink_lost"
        return None

    def retarget_playback(self, pid: int, sink_name: str) -> bool:
        """Move a RUNNING stream's playback endpoint, without closing it.

        This is C1's whole point. Today an output change goes through
        AudioEngine.retarget() == stop() + start(): PortAudio teardown,
        sd._terminate()/_initialize(), processor reset(), every buffer
        cleared, then up to 3 s of graph polling — a multi-second dropout
        and a model that has forgotten everything, for a change that does
        not concern the capture side or the model at all.

        The mechanism needed already existed: pin_process_streams() sets
        target.object on our stream nodes, and WirePlumber relinks them.
        Nothing was calling it on a stream that was already running. So the
        live path is the same metadata write, aimed at one node instead of
        two, with the capture side deliberately untouched.

        Returns False if the write could not be made (node not found, sink
        gone). False means "fall back to the full retarget", not "failed" —
        callers must keep that path, because whether WirePlumber honours a
        target change on an already-linked stream is a property of the
        running system, not something this code can guarantee.
        """
        return pin_process_streams(pid, None, sink_name)

    def diagnose_capture(self, pid: int) -> str | None:
        """Structural check on what we are actually capturing.

        See the module-level diagnose_capture() for the states and for the
        incident that motivated it. Kept separate from check() because it
        needs the caller's pid and one more graph snapshot, and because it
        answers a different question: check() asks "is the routing we set up
        still in force", this asks "is the audio reaching the model the
        audio we intended".
        """
        if not self.trap:
            return None
        return diagnose_capture(_pw_dump_all(), pid, SINK_NAME,
                                self.real.name if self.real else None)

    @property
    def monitor_source(self) -> str:
        return f"{SINK_NAME}.monitor"

    # -- engine seam: device targeting for a fresh PortAudio stream --------
    def resolve_stream_devices(self, monitor_source: str, sink_name: str) -> tuple:
        """Point a freshly-opened PortAudio stream at the trap monitor /
        real sink.

        PortAudio's "pulse" device usually resolves to pipewire-pulse,
        which honors PULSE_SOURCE/PULSE_SINK env vars — but on Ubuntu 24.04
        that device resolves to the PipeWire ALSA plugin instead, which
        ignores them, so pin_stream() below is needed as a second pass once
        the stream (and therefore its stream nodes) actually exists. Both
        env vars are still set here because they're read by whichever
        compat layer PortAudio does end up talking to, and because
        PortAudio only re-reads them on a fresh connection.
        """
        import sounddevice as sd

        os.environ["PULSE_SOURCE"] = monitor_source
        os.environ["PULSE_SINK"] = sink_name
        sd._terminate()
        sd._initialize()

        dev = next((idx for idx, d in enumerate(sd.query_devices())
                   if d["name"] == "pulse"), "default")
        return dev, dev

    def pin_stream(self, pid: int, monitor_source: str, sink_name: str) -> bool:
        # monitor_source is "<sink>.monitor"; pin_process_streams wants the
        # capture sink's own node name, not its monitor's.
        capture_sink = monitor_source.removesuffix(".monitor")
        return pin_process_streams(pid, capture_sink, sink_name)
