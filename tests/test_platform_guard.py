#!/usr/bin/env python3
"""The startup guard, and the crash it replaces.

Hardware-free and PipeWire-free by construction -- it works by taking
PipeWire *away*, so it is as meaningful on a machine that has it as on one
that does not.

The bug being pinned: backends/pipewire.py's _pw_dump() caught
subprocess.SubprocessError, but a missing binary raises FileNotFoundError,
which is an OSError and not a SubprocessError. RoutingSession.recover_stale()
runs at startup from both __main__ and ui.app, so on any machine without
pw-dump -- every Windows machine among them -- it raised before the window
opened. A packaged Windows build would have shown a crash dialog and nothing
else.
"""

import shutil
import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio import routing                      # noqa: E402
from assassin_live.audio.backends import pipewire            # noqa: E402


def test_premise():
    """If this ever fails, the bug this file exists for cannot happen and
    the rest of these tests are measuring nothing."""
    assert not isinstance(FileNotFoundError(), subprocess.SubprocessError), \
        "FileNotFoundError IS a SubprocessError -- premise of this file is wrong"
    print("  premise        -> FileNotFoundError is not a SubprocessError")


def test_no_backend_for_unknown_platform():
    assert routing._backend_for("win32") is None, "a Windows backend appeared"
    assert routing._backend_for("darwin") is None
    assert routing._backend_for("linux") is pipewire.PipeWireBackend
    print("  platform table -> linux maps to PipeWire, nothing else maps")


def test_reason_names_the_platform_when_no_backend():
    real = sys.platform
    try:
        routing.sys.platform = "win32"
        reason = routing.unsupported_reason()
        assert reason, "no reason given on a platform with no backend"
        assert "win32" in reason, f"reason does not name the platform: {reason!r}"
        # It has to be actionable, not just a refusal.
        assert "PipeWire" in reason
        print("  no backend     -> refused, and says why")
    finally:
        routing.sys.platform = real


def test_reason_names_every_missing_tool():
    real_platform, real_which = sys.platform, shutil.which

    def fake_which(cmd, *a, **kw):
        return None if cmd in pipewire.REQUIRED_TOOLS else real_which(cmd, *a, **kw)

    try:
        pipewire.sys.platform = "linux"
        pipewire.shutil.which = fake_which
        reason = pipewire.PipeWireBackend.unsupported_reason()
        assert reason, "no reason given when the PipeWire tools are absent"
        for tool in pipewire.REQUIRED_TOOLS:
            assert tool in reason, f"{tool} missing from: {reason!r}"
        print("  tools missing  -> refused, and names every one of them")
    finally:
        pipewire.sys.platform = real_platform
        pipewire.shutil.which = real_which


def _without_subprocess(fn):
    """Run fn() with every subprocess.run raising FileNotFoundError, as it
    does when the binary is not installed."""
    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])

    pipewire.subprocess.run = fake_run
    try:
        return fn()
    finally:
        pipewire.subprocess.run = real_run


def test_pw_dump_survives_a_missing_binary():
    out = _without_subprocess(lambda: pipewire._pw_dump("Sink"))
    assert out == [], f"expected [], got {out!r}"
    print("  missing pw-dump-> [] rather than FileNotFoundError")


def test_recover_stale_does_not_raise():
    """The exact startup path that used to crash: __main__ and ui.app both
    call this before anything else happens."""
    _without_subprocess(routing.RoutingSession.recover_stale)
    print("  recover_stale  -> returns instead of crashing startup")


def main():
    print("\nplatform guard")
    test_premise()
    test_no_backend_for_unknown_platform()
    test_reason_names_the_platform_when_no_backend()
    test_reason_names_every_missing_tool()
    test_pw_dump_survives_a_missing_binary()
    test_recover_stale_does_not_raise()
    print("PASS")


if __name__ == "__main__":
    main()
