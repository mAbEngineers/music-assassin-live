#!/usr/bin/env python3
"""D3 risk spike: can Python switch the Windows default output device?

Everything else in a Windows routing backend is ordinary work -- capture from
a named endpoint, mirror a volume, poll for changes. This is the one piece
with no public API. Windows exposes no documented way to set the default
audio endpoint; the standard answer is IPolicyConfig, an *undocumented* COM
interface whose CLSID/IID have shifted across Windows versions and which
Microsoft has never committed to keeping. If it cannot be driven from Python,
the backend needs a different design (a bundled helper, or shelling out), so
this is worth an hour before it is worth a week.

REPORTS, does not assert. A CI runner typically has no audio endpoints at
all, which still answers the risky half -- whether the interface exists and
can be instantiated -- even when the actual switch cannot be exercised.

Run:  python scripts/spike_d3_windows_routing.py
"""

import sys
import traceback

FINDINGS = []


def note(key, value):
    FINDINGS.append((key, value))
    print(f"  {key:<34} {value}")


def section(title):
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------- platform
def probe_platform():
    section("platform")
    note("sys.platform", sys.platform)
    note("python", sys.version.split()[0])
    if sys.platform != "win32":
        print("\n  Not Windows -- the COM probes below cannot run here.")
        print("  This script exists to be run on a Windows machine or runner.")
        return False
    import platform
    note("windows release", f"{platform.release()} / {platform.version()}")
    return True


# ------------------------------------------------------------- sounddevice
def probe_sounddevice():
    """Capture side. Expected to be the easy half -- confirm it."""
    section("sounddevice / PortAudio")
    try:
        import sounddevice as sd
    except Exception as e:
        note("import", f"FAILED: {e}")
        return
    note("version", sd.__version__)

    try:
        apis = sd.query_hostapis()
        names = [a["name"] for a in apis]
        note("host APIs", ", ".join(names) or "(none)")
        wasapi = [i for i, a in enumerate(apis) if "WASAPI" in a["name"]]
        note("WASAPI present", "yes" if wasapi else "NO -- capture plan breaks")
    except Exception as e:
        note("query_hostapis", f"FAILED: {e}")
        return

    # Does this build expose loopback? The roadmap claimed it did; 0.5.5 does
    # not. Re-check on the real platform rather than trusting either claim.
    try:
        import inspect
        sig = inspect.signature(sd.WasapiSettings.__init__)
        params = [p for p in sig.parameters if p != "self"]
        note("WasapiSettings params", ", ".join(params))
        note("loopback flag", "yes" if "loopback" in params else "NO")
    except Exception as e:
        note("WasapiSettings", f"n/a: {e}")

    try:
        devs = sd.query_devices()
        outs = [d for d in devs if d["max_output_channels"] > 0]
        ins = [d for d in devs if d["max_input_channels"] > 0]
        note("devices (out/in)", f"{len(outs)} / {len(ins)}")
        for d in list(devs)[:8]:
            print(f"      [{d['index']}] {d['name'][:44]:<44} "
                  f"in={d['max_input_channels']} out={d['max_output_channels']}")
        # The trap design depends on these two existing once VB-CABLE is in.
        cable = [d for d in devs if "CABLE" in d["name"].upper()]
        note("VB-CABLE endpoints", f"{len(cable)} found"
             if cable else "none (expected on a bare runner)")
    except Exception as e:
        note("query_devices", f"FAILED: {e}")


# -------------------------------------------------------------------- pycaw
def probe_pycaw():
    """Enumeration + volume. Documented APIs; expected to work."""
    section("pycaw (enumeration + volume)")
    try:
        from pycaw.pycaw import AudioUtilities
    except Exception as e:
        note("import", f"FAILED: {e}")
        return []
    note("import", "ok")

    devices = []
    try:
        devices = AudioUtilities.GetAllDevices()
        note("GetAllDevices()", f"{len(devices)} device(s)")
        for d in devices[:8]:
            print(f"      {str(d)[:70]}")
    except Exception as e:
        note("GetAllDevices()", f"FAILED: {e}")

    try:
        spk = AudioUtilities.GetSpeakers()
        note("GetSpeakers()", "ok -- a default render endpoint exists")
        try:
            note("default endpoint id", spk.GetId())
        except Exception as e:
            note("default endpoint id", f"could not read: {e}")
    except Exception as e:
        note("GetSpeakers()", f"FAILED (likely no audio endpoints): {e}")
    return devices


# ------------------------------------------------------------ IPolicyConfig
# The whole reason this spike exists.
POLICY_CONFIG_VARIANTS = [
    # (label, CLSID, IID)
    ("Win7+  IPolicyConfig",
     "{870af99c-171d-4f9e-af0d-e63df40c2bc9}",
     "{f8679f50-850a-41cf-9c72-430f290290c8}"),
    ("Vista  IPolicyConfigVista",
     "{294935CE-F637-4E7C-A41B-AB255460B862}",
     "{568b9108-44bf-40b4-9006-86afe5b5a620}"),
]


def build_interface(iid):
    """Declare just enough of IPolicyConfig to reach SetDefaultEndpoint.

    The ten methods before it are declared as empty placeholders purely to
    put SetDefaultEndpoint at the right vtable offset -- they are never
    called, and declaring their real signatures would be a lot of
    WAVEFORMATEX for no benefit.
    """
    from comtypes import GUID, COMMETHOD, HRESULT, IUnknown
    from ctypes import c_int, c_wchar_p

    placeholders = [
        "GetMixFormat", "GetDeviceFormat", "ResetDeviceFormat",
        "SetDeviceFormat", "GetProcessingPeriod", "SetProcessingPeriod",
        "GetShareMode", "SetShareMode", "GetPropertyValue",
        "SetPropertyValue",
    ]

    methods = [COMMETHOD([], HRESULT, name) for name in placeholders]
    methods.append(
        COMMETHOD([], HRESULT, "SetDefaultEndpoint",
                  (["in"], c_wchar_p, "deviceId"),
                  (["in"], c_int, "role"))
    )

    class IPolicyConfig(IUnknown):
        _iid_ = GUID(iid)
        _methods_ = methods

    return IPolicyConfig


def probe_policy_config():
    section("IPolicyConfig -- THE RISK")
    try:
        import comtypes
        from comtypes import GUID, CoCreateInstance
        import comtypes.client  # noqa: F401
    except Exception as e:
        note("comtypes import", f"FAILED: {e}")
        return None
    note("comtypes", getattr(comtypes, "__version__", "unknown"))

    try:
        comtypes.CoInitialize()
    except Exception:
        pass

    working = None
    for label, clsid, iid in POLICY_CONFIG_VARIANTS:
        try:
            iface = build_interface(iid)
            obj = CoCreateInstance(GUID(clsid), interface=iface)
            note(label, "INSTANTIATED ok")
            if working is None:
                working = (label, clsid, iid, obj)
        except Exception as e:
            note(label, f"failed: {type(e).__name__}: {e}")

    if working is None:
        print("\n  >>> No IPolicyConfig variant could be created.")
        print("  >>> Default-device switching from Python is NOT available")
        print("  >>> by this route. The backend needs another design.")
    return working


def endpoint_id(dev):
    """Read an endpoint's string id, whatever pycaw handed us.

    GetSpeakers() has returned two different things across pycaw versions: a
    raw IMMDevice, whose id comes from GetId(), and an AudioDevice wrapper,
    whose id is a plain .id attribute. Assuming the first one is what made
    the first real-hardware run skip the only test that mattered, so try
    both rather than pick one.
    """
    for name in ("id", "GetId"):
        try:
            v = getattr(dev, name)
        except Exception:
            continue
        try:
            v = v() if callable(v) else v
        except Exception:
            continue
        if isinstance(v, str) and v:
            return v
    return None


def probe_switch(working, devices):
    """Only meaningful where a real endpoint exists."""
    section("actually switching the default endpoint")
    if working is None:
        note("skipped", "no usable IPolicyConfig")
        return
    try:
        from pycaw.pycaw import AudioUtilities
        spk = AudioUtilities.GetSpeakers()
    except Exception as e:
        note("skipped", f"no default render endpoint on this machine ({e})")
        print("  A runner with no audio hardware cannot exercise this.")
        print("  Re-run on a real Windows desktop to close the question.")
        return

    dev_id = endpoint_id(spk)
    if not dev_id:
        note("skipped", f"could not read the endpoint id off {type(spk).__name__}")
        print(f"  attributes seen: {sorted(a for a in dir(spk) if not a.startswith('_'))[:14]}")
        print("  This is a bug in THIS script, not a limitation of the")
        print("  platform -- the endpoint exists and was found.")
        return
    note("default endpoint id", dev_id)

    label, clsid, iid, obj = working
    # Setting the CURRENT default as the default again: a real call down the
    # real vtable, which is the thing in doubt, with no user-visible
    # consequence when it works. If the ten placeholder methods have put
    # SetDefaultEndpoint at the wrong offset, this is where that shows.
    ok_roles = []
    for role, role_name in ((0, "eConsole"), (1, "eMultimedia"), (2, "eCommunications")):
        try:
            obj.SetDefaultEndpoint(dev_id, role)
            note(f"SetDefaultEndpoint({role_name})", "SUCCEEDED (no-op self-set)")
            ok_roles.append(role)
        except Exception as e:
            note(f"SetDefaultEndpoint({role_name})", f"FAILED: {e}")

    if not ok_roles:
        print("\n  >>> The call does not work. Either the vtable offset is")
        print("  >>> wrong or the interface is not what we declared.")
        print("  >>> A Windows backend needs another way to switch devices.")
        return

    # The self-set proves the call is reachable. It cannot prove the switch
    # has an EFFECT, since nothing changes. Moving the default to a different
    # endpoint and back is the only thing that does -- so it is offered, not
    # assumed, and it always puts the original back.
    if "--switch" not in sys.argv:
        print("\n  Self-set works, which proves the call is wired correctly.")
        print("  To also prove it CHANGES the default, re-run as:")
        print("      spike-windows-routing.exe --switch")
        print("  That moves your default output to another device and then")
        print("  restores it. Sound will come out of somewhere else for")
        print("  about a second.")
        return

    section("real switch (will be restored)")
    try:
        from pycaw.pycaw import AudioUtilities
        others = []
        for d in AudioUtilities.GetAllDevices():
            did = endpoint_id(d)
            if did and did != dev_id:
                others.append((did, str(d)))
        if not others:
            note("skipped", "no second endpoint to switch to")
            return
        target_id, target_name = others[0]
        note("switching to", target_name[:58])
        obj.SetDefaultEndpoint(target_id, 0)

        moved = endpoint_id(AudioUtilities.GetSpeakers())
        if moved == target_id:
            note("verified", "the default DID move -- switching works")
        else:
            note("verified", f"default is now {moved!r}, expected {target_id!r}")
    except Exception as e:
        note("switch", f"FAILED: {e}")
    finally:
        # Unconditional: a spike must not leave someone's audio rerouted.
        try:
            obj.SetDefaultEndpoint(dev_id, 0)
            back = endpoint_id(AudioUtilities.GetSpeakers())
            note("restored", "yes" if back == dev_id else f"MANUAL FIX NEEDED (now {back!r})")
        except Exception as e:
            note("restored", f"FAILED -- set your output device manually ({e})")


class _Tee:
    """Mirror everything printed into a buffer as well as the console.

    This runs as a double-clicked .exe on someone else's machine, so the
    console is where the findings appear and the report file is how they get
    back here. Writing both from one place means they cannot disagree.
    """

    def __init__(self, stream):
        self._stream = stream
        self.lines = []

    def write(self, s):
        self.lines.append(s)
        self._stream.write(s)

    def flush(self):
        self._stream.flush()


def _report_path():
    """Next to the .exe when frozen, else next to this script."""
    import pathlib
    if getattr(sys, "frozen", False):
        base = pathlib.Path(sys.executable).parent
    else:
        base = pathlib.Path(__file__).resolve().parent
    return base / "spike-windows-routing-report.txt"


def main():
    print(__doc__.split("Run:")[0].strip())
    if not probe_platform():
        return 0
    probe_sounddevice()
    devices = probe_pycaw()
    working = probe_policy_config()
    probe_switch(working, devices)

    section("verdict")
    if working:
        print("  IPolicyConfig instantiates. The risky half of D3.3 is")
        print("  viable from Python; scope the backend as planned.")
    else:
        print("  IPolicyConfig did NOT instantiate here. Confirm on real")
        print("  hardware before concluding -- a runner with the audio")
        print("  service disabled can fail this for uninteresting reasons.")
    return 0


if __name__ == "__main__":
    tee = _Tee(sys.stdout)
    sys.stdout = tee
    code = 0
    try:
        code = main()
    except Exception:
        traceback.print_exc(file=tee)
        code = 1
    finally:
        sys.stdout = tee._stream
        try:
            path = _report_path()
            path.write_text("".join(tee.lines), encoding="utf-8")
            print(f"\n  Report written to: {path}")
            print("  Send that file back -- it is the whole result.")
        except Exception as e:
            print(f"\n  (could not write report file: {e})")
        # A double-clicked console window closes the instant the process
        # ends, taking the findings with it. Only pause when a human is
        # actually watching; CI must never block here.
        if sys.stdin is not None and sys.stdin.isatty():
            try:
                input("\n  Press Enter to close...")
            except Exception:
                pass
    sys.exit(code)
