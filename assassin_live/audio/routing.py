"""Layer 1 — audio routing session.

RoutingSession is the platform-agnostic name the rest of the app (ui/app.py,
__main__.py) imports; the actual routing mechanism lives behind a
RoutingBackend (backends/base.py). Linux has exactly one backend today —
PipeWire (backends/pipewire.py) — so RoutingSession simply *is* that
backend. When a Windows backend (WASAPI loopback + pycaw) lands, this
becomes a real platform switch instead of a straight alias, e.g.:

    RoutingSession = PipeWireBackend if sys.platform.startswith("linux") else WindowsBackend

The rest of the names re-exported below are PipeWire internals that callers
and tests reach into directly (device enumeration for the output picker,
low-level sink primitives for the routing dry-run test) — re-exporting them
here means those callers don't need to know the implementation moved to
backends/pipewire.py.
"""

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

RoutingSession = PipeWireBackend
