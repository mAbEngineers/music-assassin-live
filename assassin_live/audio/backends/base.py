"""RoutingBackend — the seam between platform-agnostic code (AudioEngine,
the UI) and one platform's actual audio-interception mechanism.

PipeWire (backends/pipewire.py) is the only implementation today. A Windows
backend (WASAPI loopback capture + pycaw default-device switching) is
planned and will look nothing like PipeWire under the hood — no sinks, no
node graph, no pw-cli/wpctl — so this interface commits only to the
*behavior* callers need (insert/restore, periodic supervision, and the two
device-targeting hooks AudioEngine needs), not to how any platform achieves
it. A plain typing.Protocol rather than an ABC: backends satisfy this
structurally, no inheritance required, so a Windows backend can be written
without importing anything Linux-specific.
"""

from typing import Any, Protocol

from dataclasses import dataclass


@dataclass
class SinkInfo:
    """A platform output device, as far as the rest of the app needs to
    know: something with an id, a stable name, and a human-readable label."""
    id: int
    name: str
    description: str = ""


class RoutingBackend(Protocol):
    """enable() -> filter is inserted; disable() -> system restored.

    check() must be called periodically (~1 s) while enabled; it re-asserts
    the intended routing if something (e.g. a reconnecting Bluetooth
    headset) stole it, and reports when the real output device changed or
    vanished.
    """

    preferred_name: str | None  # user-picked output device, if any
    real: SinkInfo | None       # the current real (non-trap) output device

    @staticmethod
    def recover_stale() -> None:
        """Clean up after a crash: remove any leftover trap device and
        restore whatever was the default before the app last ran. Safe to
        call even when nothing is stale. Must work with no instance
        constructed yet — called at process startup, before enable()."""
        ...

    def enable(self) -> "SinkInfo | None":
        """Insert the trap device. Returns the real output device the
        engine should play to (None if no hardware output is currently
        available — caller should treat that as "wait and retry")."""
        ...

    def disable(self) -> None:
        """Restore the system to its pre-enable() state."""
        ...

    def check(self) -> "str | None":
        """Returns None (all good), 'real_sink_changed', or
        'real_sink_lost'."""
        ...

    @property
    def monitor_source(self) -> str:
        """What AudioEngine should capture from — whatever this platform
        calls "the trap device's own output, looped back for capture"."""
        ...

    # -- the seam AudioEngine uses instead of touching a platform API itself
    def resolve_stream_devices(self, monitor_source: str, sink_name: str) -> tuple[Any, Any]:
        """Called right before AudioEngine opens its PortAudio stream. Do
        whatever platform-level preparation freshly-created stream
        endpoints need (env vars, device-cache flushes, ...) and return the
        (capture_device, playback_device) selectors to pass to
        sounddevice.Stream(device=...)."""
        ...

    def pin_stream(self, pid: int, monitor_source: str, sink_name: str) -> bool:
        """Called right after the stream opens. Some host APIs silently
        ignore the hints given to resolve_stream_devices() for freshly
        created stream endpoints, so this is a best-effort second pass that
        retargets *this process's own* stream objects directly by pid.
        Returns whether it succeeded; a False return is a soft warning —
        the stream still runs, just possibly on the wrong device."""
        ...
