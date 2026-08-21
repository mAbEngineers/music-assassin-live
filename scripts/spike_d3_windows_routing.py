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


def probe_switch(working, devices):
    """Only meaningful where a real endpoint exists."""
    section("actually switching the default endpoint")
    if working is None:
        note("skipped", "no usable IPolicyConfig")
        return
    try:
        from pycaw.pycaw import AudioUtilities
        spk = AudioUtilities.GetSpeakers()
        dev_id = spk.GetId()
    except Exception as e:
        note("skipped", f"no default endpoint to test with ({e})")
        print("  A runner with no audio hardware cannot exercise this.")
        print("  Re-run on a real Windows desktop to close the question.")
        return

    label, clsid, iid, obj = working
    # Setting the CURRENT default as the default again: a real call through
    # the real vtable, with no user-visible consequence if it succeeds.
    for role, role_name in ((0, "eConsole"), (1, "eMultimedia"), (2, "eCommunications")):
        try:
            obj.SetDefaultEndpoint(dev_id, role)
            note(f"SetDefaultEndpoint({role_name})", "SUCCEEDED (no-op self-set)")
        except Exception as e:
            note(f"SetDefaultEndpoint({role_name})", f"FAILED: {e}")


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
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
