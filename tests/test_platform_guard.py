#!/usr/bin/env python3
"""The startup guard, and the crash it replaces.

Hardware-free and PipeWire-free by construction -- it works by taking
PipeWire *away*, so it is one of the few tests here that is just as
meaningful on a machine that has it as on one that does not.

The bug being pinned: routing._pw_dump() caught subprocess.SubprocessError,
but a missing binary raises FileNotFoundError, which is an OSError and not a
SubprocessError. So on any machine without pw-dump -- every Windows machine,
among others -- RoutingSession.recover_stale() raised at startup, before the
window opened. A packaged Windows build would have shown a crash dialog and
nothing else.
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio import routing  # noqa: E402


def _hide_pipewire(monkey_missing=True):
    """Make shutil.which() and subprocess both behave as if PipeWire is gone."""
    real_which = shutil.which

    def fake_which(cmd, *a, **kw):
        if cmd in routing.REQUIRED_TOOLS:
            return None
        return real_which(cmd, *a, **kw)

    routing.shutil.which = fake_which
    return real_which


def test_guard_reports_non_linux():
    real_platform = sys.platform
    try:
        routing.sys.platform = "win32"
        reason = routing.unsupported_reason()
        assert reason, "no reason given on a non-Linux platform"
        assert "win32" in reason, f"reason does not name the platform: {reason!r}"
        # It has to be something a user can act on, not just a refusal.
        assert "PipeWire" in reason
        print("  non-linux      -> refused, and says why")
    finally:
        routing.sys.platform = real_platform


def test_guard_reports_missing_pipewire_on_linux():
    real_platform, real_which = sys.platform, shutil.which
    try:
        routing.sys.platform = "linux"
        _hide_pipewire()
        reason = routing.unsupported_reason()
        assert reason, "no reason given when the PipeWire tools are absent"
        for tool in routing.REQUIRED_TOOLS:
            assert tool in reason, f"{tool} missing from: {reason!r}"
        print("  linux, no pw   -> refused, and names every missing tool")
    finally:
        routing.sys.platform = real_platform
        routing.shutil.which = real_which


def test_pw_dump_survives_a_missing_binary():
    """The actual regression. Must return empty, not raise."""
    import subprocess
    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])

    routing.subprocess.run = fake_run
    try:
        assert not isinstance(FileNotFoundError(), subprocess.SubprocessError), \
            "premise of this test is wrong: FileNotFoundError IS a SubprocessError"
        out = routing._pw_dump("Sink")
        assert out == [], f"expected [], got {out!r}"
        print("  missing pw-dump-> [] rather than FileNotFoundError")
    finally:
        routing.subprocess.run = real_run


def test_recover_stale_does_not_raise_without_pipewire():
    """The exact startup path that used to crash: __main__ and ui.app both
    call this before anything else happens."""
    import subprocess
    real_run = subprocess.run
    routing.subprocess.run = lambda cmd, *a, **kw: (_ for _ in ()).throw(
        FileNotFoundError(2, "No such file or directory", cmd[0]))
    try:
        routing.RoutingSession.recover_stale()
        print("  recover_stale  -> returns instead of crashing startup")
    finally:
        routing.subprocess.run = real_run


def main():
    print("\nplatform guard")
    test_guard_reports_non_linux()
    test_guard_reports_missing_pipewire_on_linux()
    test_pw_dump_survives_a_missing_binary()
    test_recover_stale_does_not_raise_without_pipewire()
    print("PASS")


if __name__ == "__main__":
    main()
