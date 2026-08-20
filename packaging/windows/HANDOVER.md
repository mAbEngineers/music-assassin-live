# Windows packaging — handover

Written 2026-08-13. Covers the Windows/mobile packaging investigation and
the scaffolding built from it.

> **Status 2026-08-20.** The investigation below still stands and is worth
> reading — it is the record of why VB-CABLE, why not Synchronous Audio
> Router, and why Inno Setup 6.4.3. Three things in it are now out of date:
> the files *are* committed (this branch); the routing seam *does* exist
> (`backends/base.py`, ROADMAP D2), so the backend is one new file rather
> than a fork; and the VB-CABLE licensing question is **settled** by
> downloading at install time against a pinned SHA-256 instead of bundling,
> which makes the permission email optional rather than blocking. The
> "WASAPI loopback capture" phrasing throughout is also wrong — see ROADMAP
> D3. **The current plan is ROADMAP D3 and D4; this file is history.**

## Where each platform actually stands

- **Linux** — done. `.deb` via `scripts/build_deb.sh`, working today.
- **Android / iOS** — not pursued, blocked at the OS level, not a packaging
  problem. Android's `AudioPlaybackCapture` API requires the *source* app to
  opt in (most music/streaming apps refuse); custom effects on the global
  output mix need root or vendor/ROM integration. iOS can't capture
  system audio at all. Full detail already lives in
  `docs/ARCHITECTURE.md` §6 and `Music-Assassin/docs/REALTIME_MUSIC_REMOVAL_BUILD_GUIDE.md`
  §10–11 — nothing new to hand over here, this was research only, no code.
- **Windows** — in progress. Unlike mobile, nothing about Windows is
  OS-blocked: WASAPI loopback capture is open to any app, no opt-in needed.
  The gap is entirely engineering + one licensing question. Rest of this doc
  covers that.

## What exists now (this session's output, all untested)

| File | Purpose |
|---|---|
| `packaging/windows/music-assassin-live.iss` | Inno Setup script: installs the exe + models, sets `MUSIC_ASSASSIN_MODELS` env var, conditionally bundles+silently-runs VB-CABLE, else prompts the user to grab it manually |
| `scripts/build_windows.bat` | PyInstaller build (mirrors `scripts/build_deb.sh`'s flags) + stages models + invokes Inno Setup's `ISCC.exe`. **Must run on an actual Windows machine** — PyInstaller does not cross-compile, and this repo has no Windows/Wine environment to run it in |
| `packaging/windows/vb-audio-permission-email.md` | Drafted, reviewed for accuracy, **not sent**. Asks VB-Audio for redistribution permission |
| `packaging/windows/README.md` | Build instructions + prerequisites for whoever runs the build |
| `packaging/icons/music-assassin-live.ico` | Generated from the existing source PNG, multi-res (256→16px), verified valid |
| `.gitignore` | Added `packaging/windows/vendor/*.exe` so the actual VB-Cable binary (if a maintainer drops it in locally) never gets committed |

All of the above are **untracked in git** — `git status` still shows them as
`??`. Nothing has been committed or pushed.

## Key findings from research (condensed — full reasoning is earlier in this conversation)

- **VB-CABLE licensing**: free to use, but VB-Audio's terms ask for a
  distribution agreement once you're bundling/redistributing above
  personal-use volume (their threshold: >10 units). Silently automating the
  install doesn't change that classification — you'd still be the one
  pushing their binary to every user. Resolution path: send the drafted
  email, or ship the unbundled fallback (installer prompts the user to
  install VB-CABLE themselves) indefinitely.
- **Inno Setup version**: pin to **6.4.3**. 6.5.0+ added a paid
  commercial-license tier; 6.4.3 is the last version unambiguous for a free,
  non-commercial, open-source build.
- **Synchronous Audio Router** (GPL, fully open-source alternative to
  VB-CABLE) — investigated and **rejected**. It needs an ASIO host
  component this app doesn't have (real engineering, not a swap), its
  unsigned-driver install path is arguably worse UX than VB-CABLE's, and it
  would pull in the Steinberg ASIO SDK's own license terms — net effect:
  more work, more licensing surface, not less.
- **Music-Assassin's existing `.exe`** (sibling research repo, `build/*.spec`
  + `build.bat`) is unrelated prior art — it's a file-in/file-out batch
  separator (Demucs/Spleeter/MDX-Net) with no system-audio-routing problem
  to solve. Doesn't transfer to this app's real-time routing needs beyond
  "yes, PyInstaller-on-Windows works, run it on an actual Windows box."

## What's NOT done — the actual blocking gap

**The Windows audio-routing backend in `assassin_live` does not exist.**
`assassin_live/audio/routing.py` and `engine.py` only know how to talk to
PipeWire/WirePlumber (`pw-cli` / `wpctl` / `pw-dump`). There is no Windows
equivalent written — no WASAPI loopback capture path, no code to set
VB-CABLE as the default output device (the natural tool for that is `pycaw`,
MIT-licensed, Windows Core Audio API bindings — not yet a dependency).
**This means: even with a perfectly built installer, the installed app would
not function on Windows today.** The packaging work in this session is
scaffolding built ahead of that — it doesn't complete Windows support by
itself, and I should not be understood as having claimed it does.

Also not done:
- No `.exe` has been built. No Windows machine, VM, or Wine is available in
  this environment to build or test one.
- The VB-Audio permission email has not been sent.
- The `.iss` script has never been run through `ISCC.exe` — it's a
  careful-but-unverified first draft. Two spots most likely to need fixing
  on first real compile: the `SendMessageTimeoutA` external declaration and
  the `ShellExecAsOriginalUser` call signature (both hand-written against
  documented Inno Setup Pascal Script APIs, neither exercised).
- `assassin_live/paths.py`'s `data_dir()`/`state_dir()`/`config_dir()` use
  `~/.local/share/...`-style paths. On Windows these resolve harmlessly
  under `C:\Users\<name>\.local\share\music-assassin` — functional, but not
  an idiomatic Windows location (`%APPDATA%` would be the norm). Nobody has
  decided whether that's worth changing.

## Recommended next steps, in order

1. **Decide on the VB-Audio email** — send `vb-audio-permission-email.md` as
   drafted (edit signature/contact first), or make the call to ship
   unbundled indefinitely and stop treating it as an open question.
2. **Write the Windows routing backend** — this is the real unblock, not the
   installer. New capture path (WASAPI loopback, `sounddevice` already
   supports it) + default-device switching (`pycaw`) + whatever crash/state
   recovery equivalent to `routing.py`'s PipeWire logic is needed. This is
   real engineering (days, not hours) — worth scoping as its own task before
   starting.
3. **Get a Windows build environment** — either a real Windows
   machine/VM to run `scripts\build_windows.bat` by hand, or a GitHub
   Actions `windows-latest` CI job (offered last session, user deferred —
   still on the table). Needed to find out whether the `.iss` script
   actually compiles as written.
4. **Once (2) and (3) both work**, commit the `packaging/windows/` +
   `scripts/build_windows.bat` files — currently sitting untracked on
   purpose, since committing packaging for an app that can't run yet would
   be misleading.

## Open questions for whoever picks this up

- Is the "prompt user to install VB-CABLE manually" fallback acceptable as
  the *shipped* experience, or is bundling (post-permission) a hard
  requirement before Windows is considered "supported"?
- Priority: routing backend first (makes the app work, installer is
  cosmetic until then) vs. installer validation first (de-risks the
  packaging side while routing is being built in parallel)?
