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
        """Returns None (all good) or one of:

        'real_sink_changed'   someone made another device the system
                              default — treat as explicit user intent:
                              follow it, and remember it.
        'real_sink_replaced'  the device we were playing to vanished and a
                              fallback was chosen. Follow it, but do NOT
                              remember it — the user's choice did not
                              change, their hardware did.
        'real_sink_lost'      it vanished and there is nothing to fall back
                              to.
        'trap_lost'           the interception device itself is gone —
                              nothing can be re-asserted, the session has to
                              be rebuilt or stopped.
        """
        ...

    def retarget_playback(self, pid: int, sink_name: str) -> bool:
        """Move a running stream's playback endpoint to `sink_name` without
        closing it, leaving capture and the processor's state alone.

        Separate from pin_stream() even where a platform implements both the
        same way, because they answer different questions and will diverge:
        pin_stream is a best-effort fixup for a stream that just opened,
        this is a live move of one that is mid-playback. On Windows the
        first may be a no-op while this one is a real device switch.

        Returns False when the move could not be made — which means "use the
        stop/start path instead", not "the audio is broken". Callers must
        keep that fallback: whether a platform honours a retarget on an
        already-linked stream is a property of the running system, not a
        guarantee this interface can make.
        """
        ...

    def diagnose_capture(self, pid: int) -> "str | None":
        """Is the audio reaching the processor the audio we intended?

        check() asks whether the routing we *set up* is still in force;
        this asks whether what we are *capturing* is still what we meant to
        capture. They come apart: a platform is free to re-attach an
        orphaned capture stream somewhere else entirely, leaving a stream
        that is alive, healthy, and wired to the wrong thing.

        Returns None when correct or not yet judgeable, else 'trap_lost',
        'feedback_loop' (capturing the monitor of the device we play into —
        output feeds input, unconditionally wrong and audible), or
        'capture_hijacked' (capturing something else entirely).

        Every platform that intercepts audio can reach these states, so this
        belongs on the interface rather than in the PipeWire backend alone;
        a backend with no way to inspect its graph may return None always.
        """
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
