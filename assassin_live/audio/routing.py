"""Layer 1 — audio routing session.

RoutingSession is the platform-agnostic name the rest of the app (ui/app.py,
__main__.py) imports; the actual routing mechanism lives behind a
RoutingBackend (backends/base.py). Linux has exactly one backend today —
PipeWire (backends/pipewire.py) — so RoutingSession simply *is* that
backend. The platform switch below is a real switch now rather than a
straight alias, with one entry in its table: adding a Windows backend means
adding a row to _BACKEND_BY_PLATFORM, not editing the callers.

unsupported_reason() is the other half of it. A platform with no backend and
a platform whose backend is missing its dependencies are different failures,
and both have to produce a sentence a user can act on rather than a
traceback from deep inside a subprocess call.

The rest of the names re-exported below are PipeWire internals that callers
and tests reach into directly (device enumeration for the output picker,
low-level sink primitives for the routing dry-run test) — re-exporting them
here means those callers don't need to know the implementation moved to
backends/pipewire.py.
"""

import sys

from .backends.base import RoutingBackend, SinkInfo
from .backends.pipewire import (
    PipeWireBackend,
    SINK_DESC,
    SINK_NAME,
    create_trap_sink,
    destroy_node,
    find_sinks_named,
    get_default_sink,
    list_sinks,
    set_default,
)

_BACKEND_BY_PLATFORM = (
    ("linux", PipeWireBackend),
)


def _backend_for(platform: str):
    for prefix, backend in _BACKEND_BY_PLATFORM:
        if platform.startswith(prefix):
            return backend
    return None


def unsupported_reason() -> str | None:
    """Why audio routing cannot work on this machine, or None when it can.

    Checked once at startup, before anything touches the platform's audio
    graph. Two different failures arrive here and they need different
    wording: a platform with no backend at all, and a platform whose backend
    exists but whose dependencies are missing. The second is the backend's
    own question, so it is asked rather than guessed at.
    """
    backend = _backend_for(sys.platform)
    if backend is None:
        return (
            "Music Assassin Live does not support this platform yet.\n\n"
            "It routes audio through PipeWire, which is Linux-only. A "
            "Windows version needs a different audio backend (WASAPI plus a "
            "virtual output device) and that work is not finished, so there "
            f"is nothing here that would work on {sys.platform}.\n\n"
            "Windows packaging exists in this repository, but it builds an "
            "app that cannot route audio -- see packaging/windows/README.md."
        )
    return backend.unsupported_reason()


# Importable on every platform, so callers (ui/app.py, __main__.py) need no
# platform branch of their own. unsupported_reason() above is what stops it
# being constructed somewhere it cannot work; the fallback here only keeps
# the import resolving, and is never reached past that guard.
RoutingSession = _backend_for(sys.platform) or PipeWireBackend
