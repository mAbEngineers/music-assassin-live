#!/usr/bin/env bash
# Builds a .deb for Ubuntu/Debian: PyInstaller onefile binary (bundles the
# no-torch Python stack, onnxruntime, and tkinter) + a minimal DEBIAN control
# tree. Depends: libportaudio2 — sounddevice locates it via
# ctypes.util.find_library() at runtime, which only checks the system
# ldconfig cache (not PyInstaller's bundle dir), so it must be a real
# installed package, not just bundled into the onefile archive.
# Bundles every model whose upstream license permits redistribution
# (gtcrn, dpdfnet, dpdfnet_hr, dtln — see models/README.md) into
# /usr/share/music-assassin-live/models, which assassin_live/paths.py checks
# as a system-wide fallback — so the default model works immediately after
# install, with zero manual steps. speechdenoiser is excluded: its upstream
# license is unresolved and it must not ship as a release asset.
#
# Usage: scripts/build_deb.sh [MODEL_SOURCE_DIR]
#   MODEL_SOURCE_DIR defaults to $MUSIC_ASSASSIN_MODELS, then
#   ~/.local/share/music-assassin/models (wherever import_models.py put them).
# Output: dist/music-assassin-live_<version>_<arch>.deb
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PKG=music-assassin-live
VENV="$ROOT/.venv"
# Overridable so the scratch tree can live on local disk. Building on a
# network mount fails in ways that look nothing like a packaging bug: objcopy
# cannot rewrite ELF sections in place there ("Operation not supported"), and
# PyInstaller's --add-data refuses any source path containing a colon, which
# a gvfs/sftp mount point has. The finished .deb still lands in dist/.
BUILD_DIR="${BUILD_DIR:-$ROOT/build}"
DIST_DIR="$ROOT/dist"
STAGE="$BUILD_DIR/deb-root"
MAINTAINER="${DEB_MAINTAINER:-A-Ahmad-02 <a.ahmad.mab@gmail.com>}"
MODEL_SOURCE_DIR="${1:-${MUSIC_ASSASSIN_MODELS:-$HOME/.local/share/music-assassin/models}}"

# name -> (onnx files..., model_card.json, license text file in packaging/model-licenses)
REDISTRIBUTABLE_MODELS=(
    "gtcrn_simple.onnx|gtcrn_simple.json|MIT-gtcrn.txt"
    "dpdfnet_baseline.onnx|dpdfnet_baseline.json|Apache-2.0-dpdfnet.txt"
    "dpdfnet2_48khz_hr.onnx|dpdfnet2_48khz_hr.json|Apache-2.0-dpdfnet.txt"
    "dtln_model_1.onnx dtln_model_2.onnx|dtln.json|MIT-dtln.txt"
)
DEFAULT_MODEL_FILE="dpdfnet2_48khz_hr.onnx"
ICON_SIZES=(16 22 24 32 48 64 128 256)

VERSION="$(python3 -c "
import re, pathlib
text = pathlib.Path('assassin_live/__init__.py').read_text()
print(re.search(r'__version__ = \"([^\"]+)\"', text).group(1))
")"
ARCH="$(dpkg --print-architecture)"

echo "==> building ${PKG} ${VERSION} (${ARCH})"

if [ ! -x "$VENV/bin/python" ]; then
    echo "error: no .venv found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

"$VENV/bin/python" -c "import PyInstaller" 2>/dev/null || {
    echo "==> installing pyinstaller (build-time only, not part of requirements.txt)"
    "$VENV/bin/pip" install pyinstaller
}

# Icon downscaler. ImageMagick is the intended one; ffmpeg is accepted as a
# fallback because it is already present on machines that touch audio and
# does the same Lanczos resize, which spares a sudo apt-get on a build box
# that has everything else it needs.
if command -v convert >/dev/null 2>&1; then
    ICON_TOOL=convert
elif command -v ffmpeg >/dev/null 2>&1; then
    ICON_TOOL=ffmpeg
else
    echo "error: need ImageMagick 'convert' or 'ffmpeg' to render icon sizes." >&2
    exit 1
fi

render_icon() {  # src size dest
    if [ "$ICON_TOOL" = convert ]; then
        convert "$1" -filter Lanczos -resize "${2}x${2}" "$3"
    else
        ffmpeg -v error -y -i "$1" -vf "scale=${2}:${2}:flags=lanczos" \
            -pix_fmt rgba "$3"
    fi
}

rm -rf "$STAGE" "$BUILD_DIR/pyinstaller"
mkdir -p "$STAGE/DEBIAN" \
         "$STAGE/usr/bin" \
         "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/doc/$PKG/model-licenses" \
         "$STAGE/usr/share/music-assassin-live/models" \
         "$STAGE/usr/share/pixmaps"
for sz in "${ICON_SIZES[@]}"; do
    mkdir -p "$STAGE/usr/share/icons/hicolor/${sz}x${sz}/apps"
done

echo "==> running PyInstaller"
# Module form, not $VENV/bin/pyinstaller: console-script shebangs hold the
# absolute path the venv was created at, so they stop working the moment the
# checkout is reached by another path (a network mount, a moved directory).
# python -m has no shebang to go stale.
"$VENV/bin/python" -m PyInstaller --name "$PKG" \
    --onefile \
    --collect-all onnxruntime \
    --hidden-import tkinter \
    --add-data "$ROOT/assassin_live/ui/assets/icon.png:." \
    --add-data "$ROOT/assassin_live/ui/assets/icon_32.png:." \
    --add-data "$ROOT/assassin_live/ui/assets/icon_48.png:." \
    --add-data "$ROOT/assassin_live/ui/assets/icon_64.png:." \
    --paths "$ROOT" \
    -y \
    --distpath "$BUILD_DIR/pyinstaller/dist" \
    --workpath "$BUILD_DIR/pyinstaller/work" \
    --specpath "$BUILD_DIR/pyinstaller" \
    "$ROOT/scripts/run_assassin_live.py" > "$BUILD_DIR/pyinstaller.log" 2>&1 \
    || { tail -60 "$BUILD_DIR/pyinstaller.log"; exit 1; }

install -m 755 "$BUILD_DIR/pyinstaller/dist/$PKG" "$STAGE/usr/bin/$PKG"
install -m 644 "$ROOT/packaging/$PKG.desktop" "$STAGE/usr/share/applications/$PKG.desktop"
install -m 644 "$ROOT/LICENSE" "$STAGE/usr/share/doc/$PKG/copyright"
install -m 755 "$ROOT/packaging/postinst" "$STAGE/DEBIAN/postinst"
install -m 755 "$ROOT/packaging/postrm" "$STAGE/DEBIAN/postrm"

echo "==> rendering icon sizes"
ICON_SRC="$ROOT/packaging/icons/${PKG}-1024.png"
for sz in "${ICON_SIZES[@]}"; do
    render_icon "$ICON_SRC" "$sz" \
        "$STAGE/usr/share/icons/hicolor/${sz}x${sz}/apps/$PKG.png"
done
install -m 644 "$STAGE/usr/share/icons/hicolor/64x64/apps/$PKG.png" \
    "$STAGE/usr/share/pixmaps/$PKG.png"

echo "==> bundling redistributable models from $MODEL_SOURCE_DIR"
bundled_any_model=false
default_model_bundled=false
for entry in "${REDISTRIBUTABLE_MODELS[@]}"; do
    IFS='|' read -r onnx_files card license_file <<< "$entry"
    missing=false
    for f in $onnx_files; do
        [ -f "$MODEL_SOURCE_DIR/$f" ] || missing=true
    done
    if [ "$missing" = true ]; then
        echo "    skip: $onnx_files not found in $MODEL_SOURCE_DIR"
        continue
    fi
    for f in $onnx_files; do
        install -m 644 "$MODEL_SOURCE_DIR/$f" "$STAGE/usr/share/music-assassin-live/models/$f"
        [ "$f" = "$DEFAULT_MODEL_FILE" ] && default_model_bundled=true
    done
    install -m 644 "$ROOT/models/$card" "$STAGE/usr/share/music-assassin-live/models/$card"
    install -m 644 "$ROOT/packaging/model-licenses/$license_file" \
        "$STAGE/usr/share/doc/$PKG/model-licenses/$license_file"
    bundled_any_model=true
    echo "    bundled: $onnx_files"
done

if [ "$bundled_any_model" = false ]; then
    echo "error: no redistributable models found in $MODEL_SOURCE_DIR" >&2
    echo "       run scripts/import_models.py --source ../Music-Assassin/models first," >&2
    echo "       or pass a source dir: scripts/build_deb.sh /path/to/models" >&2
    exit 1
fi
if [ "$default_model_bundled" = false ]; then
    echo "error: default model ($DEFAULT_MODEL_FILE) missing from $MODEL_SOURCE_DIR — refusing to ship a package with no working default" >&2
    exit 1
fi

cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: sound
Priority: optional
Architecture: $ARCH
Depends: libportaudio2
Recommends: pipewire, wireplumber
Maintainer: $MAINTAINER
Description: System-wide realtime music removal for Linux
 One toggle strips background music from everything the device plays,
 before it reaches the speaker. Uses PipeWire trap-sink routing plus an
 ONNX speech-enhancement model (onnxruntime, no torch).
 .
 Ships with the openly-licensed models (gtcrn, dpdfnet, dpdfnet_hr, dtln —
 see /usr/share/doc/music-assassin-live/model-licenses) preinstalled and
 ready to use. speechdenoiser is excluded (unresolved upstream license);
 add it yourself in ~/.local/share/music-assassin/models/ if wanted.
EOF

mkdir -p "$DIST_DIR"
DEB_FILE="$DIST_DIR/${PKG}_${VERSION}_${ARCH}.deb"
rm -f "$DEB_FILE"
dpkg-deb --build --root-owner-group "$STAGE" "$DEB_FILE"

echo "==> built $DEB_FILE"
echo "    install: sudo apt install $DEB_FILE"
echo "    (or)   : sudo dpkg -i $DEB_FILE"
echo "    ready to use out of the box — default model ($DEFAULT_MODEL_FILE) is bundled"
