"""Layer 1 — PipeWire trap-sink routing (Linux).

Volume-booster behavior: on enable we create a null "trap" sink, make it the
default so WirePlumber migrates every default-following app stream into it,
then the engine captures the trap's monitor and plays the processed result to
the real hardware sink. Disable restores everything.

Uses only pw-cli / wpctl / pw-dump — no pactl (not installed on target dev
machine, PipeWire 1.0.5). Crash recovery: the previous default sink is
persisted to a state file *before* the swap; recover_stale() runs at startup.
"""

import json
import re
import subprocess
import time
from dataclasses import dataclass

from ..paths import ROUTING_STATE

SINK_NAME = "MusicAssassin"
SINK_DESC = "Music Assassin"


@dataclass
class SinkInfo:
    id: int
    name: str
    description: str = ""


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout


def _pw_dump_nodes() -> list[dict]:
    try:
        objs = json.loads(_run(["pw-dump"]))
    except (json.JSONDecodeError, subprocess.SubprocessError):
        return []
    return [o for o in objs if o.get("type") == "PipeWire:Interface:Node"]


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


class RoutingSession:
    """enable() -> filter is inserted; disable() -> system restored.

    check() must be called periodically (~1 s) while enabled; it re-asserts
    the default sink if something (e.g. a reconnecting Bluetooth headset)
    stole it, and reports when the real output sink changed or vanished.
    """

    def __init__(self):
        self.trap: SinkInfo | None = None
        self.real: SinkInfo | None = None

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
        """Returns None (all good), 'real_sink_changed', or 'real_sink_lost'."""
        if not self.trap:
            return None
        default = get_default_sink()
        if default is None or default.name != SINK_NAME:
            # BT reconnect etc. stole the default — grab the new device as our
            # output, then re-assert the trap.
            if default is not None:
                changed = self.real is None or default.name != self.real.name
                self.real = default
                set_default(self.trap.id)
                if changed:
                    return "real_sink_changed"
            set_default(self.trap.id)
            return None
        # default is still us; make sure our output device still exists
        if self.real and not any(s.name == self.real.name for s in list_sinks()):
            self.real = next(
                (s for s in list_sinks() if s.name != SINK_NAME), None)
            return "real_sink_changed" if self.real else "real_sink_lost"
        return None

    @property
    def monitor_source(self) -> str:
        return f"{SINK_NAME}.monitor"
