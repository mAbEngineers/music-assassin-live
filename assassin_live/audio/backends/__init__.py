"""Layer 1 backend implementations — one module per platform.

backends.base defines the RoutingBackend seam; backends.pipewire is the
(currently only) Linux implementation. See ../routing.py for the
platform-agnostic RoutingSession name the rest of the app imports.
"""
