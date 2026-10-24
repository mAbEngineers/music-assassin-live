# Windows packaging (scaffolding, not yet a working build)

Builds `dist/music-assassin-live-setup-<version>.exe`: a PyInstaller onefile
binary (same engine as the Linux `.deb`, see `scripts/build_deb.sh`) wrapped
in an Inno Setup installer.

## Status: packaging is ready, the app itself is not

`assassin_live/audio/routing.py` and `engine.py` only know how to talk to
PipeWire/WirePlumber. There is no Windows routing backend yet (WASAPI
loopback capture + setting a virtual device as the Windows default — the
Windows equivalent of the PipeWire trap-sink). Running the installed `.exe`
today will not work end-to-end. This directory exists so the packaging side
is ready the moment that routing code lands — it does not itself add Windows
support.

## Before shipping a build with VB-CABLE bundled

VB-CABLE (the virtual audio driver used to replace the Windows default
output device, same role PipeWire's trap sink plays on Linux) is free to use
but its licensing terms ask for a distribution agreement above personal-use
volume — see https://vb-audio.com/Services/licensing.htm. **Do not put
`VBCABLE_Setup_x64.exe` in `vendor/` and ship publicly until that's
resolved.** Draft outreach: `vb-audio-permission-email.md` in this
directory — review, edit, and send it yourself; nothing here sends it for
you.

Until that's settled, leave `vendor/` empty. The installer still builds and
works — it just falls back to prompting the user to install VB-CABLE
manually from vb-audio.com on first run, the same "documented external
dependency" shape the Linux build uses for PipeWire.

## Prerequisites (on a Windows machine — PyInstaller does not cross-compile)

- Python 3.10+, with `.venv` set up and `requirements.txt` installed
  (`python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`)
- [Inno Setup 6.4.3](https://jrsoftware.org/isdl.php) — pin to this version.
  6.5.0+ introduced a paid commercial-license tier; 6.4.3 is the last
  version with zero ambiguity for a free, non-commercial, open-source build
  like this one. Install with `ISCC.exe` on `PATH`, or leave it at the
  default `C:\Program Files (x86)\Inno Setup 6\` — the build script checks
  both.
- Models: same redistributable set `scripts/build_deb.sh` uses (gtcrn,
  dpdfnet, dpdfnet_hr, dtln — `speechdenoiser` excluded, see
  `models/README.md`). Run `scripts\import_models.py` first, or pass a
  source dir directly.

## Build

```batch
scripts\build_windows.bat [MODEL_SOURCE_DIR]
```

Output: `dist\music-assassin-live-setup-<version>.exe`

## What the installer does

- Installs the app to `Program Files\Music Assassin Live`
- Bundles the redistributable models, and sets a per-user
  `MUSIC_ASSASSIN_MODELS` environment variable pointing at them —
  `assassin_live/paths.py` already checks that variable first, so no code
  change was needed for Windows model discovery
- If `packaging\windows\vendor\VBCABLE_Setup_x64.exe` is present at build
  time: bundles it and runs it silently (`-i -h`) during setup. Windows will
  still show one unavoidable driver-signing security prompt — that's an OS
  security gate, not something any installer flag can suppress
- If not present: skips bundling, and after install offers to open the
  VB-CABLE download page for the user
- Start Menu + optional desktop shortcut, standard uninstaller

## Icon

`packaging/icons/music-assassin-live.ico` is generated from the existing
`assassin_live/ui/assets/icon.png` / `packaging/icons/music-assassin-live-1024.png`:

```bash
convert packaging/icons/music-assassin-live-1024.png \
  -define icon:auto-resize=256,128,64,48,32,16 \
  packaging/icons/music-assassin-live.ico
```

Already generated and committed — regenerate only if the source icon
changes.
