@echo off
REM Builds the Windows installer: PyInstaller exe (same engine as the Linux
REM build, bundles onnxruntime + tkinter, no system deps) + an Inno Setup
REM installer that gets VB-CABLE onto the machine, giving the app a Windows
REM equivalent of the PipeWire trap sink used on Linux.
REM
REM SCOPE: this builds a package, not a working app. The Windows routing
REM backend does not exist yet (ROADMAP D3.3) — backends/base.py has the
REM seam, nothing implements it for Windows. Do not read a successful build
REM here as Windows support.
REM
REM VB-CABLE: the public build should download it at install time against a
REM pinned SHA-256 rather than bundle it (ROADMAP D3.4). BundleVBCable is
REM for internal builds only — publishing one is redistribution whatever the
REM output file is named.
REM
REM TODO (ROADMAP D3.5): reconsider --onefile. Unsigned onefile binaries draw
REM SmartScreen warnings and re-extract to %TEMP% on every launch; --onedir
REM avoids both.
REM
REM Must run ON Windows — PyInstaller does not cross-compile. Requires:
REM   - a .venv with requirements.txt installed (python -m venv .venv)
REM   - pyinstaller (installed automatically below if missing)
REM   - Inno Setup 6.4.3 (ISCC.exe on PATH or in the default install dir) —
REM     see packaging/windows/README.md for why 6.4.3 specifically
REM
REM Usage: scripts\build_windows.bat [MODEL_SOURCE_DIR]
REM   MODEL_SOURCE_DIR defaults to %MUSIC_ASSASSIN_MODELS%, then
REM   %USERPROFILE%\.local\share\music-assassin\models (same convention as
REM   assassin_live\paths.py and scripts\import_models.py).
REM Output: dist\music-assassin-live-setup-<version>.exe

setlocal enabledelayedexpansion

set "ROOT=%~dp0.."
pushd "%ROOT%"

set "PKG=music-assassin-live"
set "VENV=%ROOT%\.venv"
set "BUILD_DIR=%ROOT%\build"
set "DIST_DIR=%ROOT%\dist"
set "STAGE=%BUILD_DIR%\win-stage"
set "WIN_PKG_DIR=%ROOT%\packaging\windows"

if "%~1"=="" (
    if defined MUSIC_ASSASSIN_MODELS (
        set "MODEL_SOURCE_DIR=%MUSIC_ASSASSIN_MODELS%"
    ) else (
        set "MODEL_SOURCE_DIR=%USERPROFILE%\.local\share\music-assassin\models"
    )
) else (
    set "MODEL_SOURCE_DIR=%~1"
)

if not exist "%VENV%\Scripts\python.exe" (
    echo error: no .venv found. Run: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)

REM Read __version__ without a for/f loop. cmd's for/f mangles a command
REM containing both parentheses and nested quotes -- the previous one-liner
REM died with "VERSION was unexpected at this time" the first time this
REM script was ever run. chr(34) keeps a double quote off the command line
REM entirely, and the temp file avoids for/f altogether.
set "VERFILE=%TEMP%\music-assassin-version.txt"
"%VENV%\Scripts\python.exe" -c "import pathlib;print([l.split('=')[1].strip().strip(chr(34)) for l in pathlib.Path('assassin_live/__init__.py').read_text().splitlines() if l.startswith('__version__')][0])" > "%VERFILE%"
if errorlevel 1 (
    echo error: could not read __version__ from assassin_live\__init__.py >&2
    exit /b 1
)
set /p VERSION=<"%VERFILE%"
del "%VERFILE%" >nul 2>nul
if not defined VERSION (
    echo error: __version__ came back empty >&2
    exit /b 1
)
echo ==^> building %PKG% %VERSION% (windows)

"%VENV%\Scripts\python.exe" -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo ==^> installing pyinstaller ^(build-time only^)
    "%VENV%\Scripts\pip.exe" install pyinstaller
)

if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%\models" "%STAGE%\model-licenses"

echo ==^> running PyInstaller
set "ICON_ARG="
if exist "%ROOT%\packaging\icons\%PKG%.ico" set "ICON_ARG=--icon "%ROOT%\packaging\icons\%PKG%.ico""

"%VENV%\Scripts\pyinstaller.exe" --name "%PKG%" ^
    --onefile ^
    --windowed ^
    --collect-all onnxruntime ^
    --hidden-import tkinter ^
    --add-data "%ROOT%\assassin_live\ui\assets\icon.png;." ^
    --paths "%ROOT%" ^
    %ICON_ARG% ^
    -y ^
    --distpath "%BUILD_DIR%\pyinstaller\dist" ^
    --workpath "%BUILD_DIR%\pyinstaller\work" ^
    --specpath "%BUILD_DIR%\pyinstaller" ^
    "%ROOT%\scripts\run_assassin_live.py"
if errorlevel 1 (
    echo PyInstaller build failed.
    exit /b 1
)

echo ==^> staging models from %MODEL_SOURCE_DIR%
REM Same redistributable set as scripts/build_deb.sh — speechdenoiser is
REM excluded, its upstream license is unresolved (see models/README.md).
set "BUNDLED_ANY=0"
call :stage_model gtcrn_simple.onnx gtcrn_simple.json MIT-gtcrn.txt
call :stage_model dpdfnet_baseline.onnx dpdfnet_baseline.json Apache-2.0-dpdfnet.txt
call :stage_model dpdfnet2_48khz_hr.onnx dpdfnet2_48khz_hr.json Apache-2.0-dpdfnet.txt
call :stage_dtln

if "%BUNDLED_ANY%"=="0" (
    if "%ALLOW_NO_MODELS%"=="1" (
        REM CI compile-check mode. The .onnx weights are release assets and are
        REM not in the repository, so a CI runner has none to stage. Building
        REM without them still proves the exe links and the .iss compiles,
        REM which is the only thing CI can prove. It does NOT produce a
        REM shippable installer -- the stub file below is what tells anyone
        REM who installs one by mistake why it cannot process audio.
        echo warning: no models staged -- ALLOW_NO_MODELS=1 is set, so this is a
        echo          compile check only. The installer it produces is NOT shippable.
        > "%STAGE%\models\NO-MODELS-IN-THIS-BUILD.txt" echo This build carries no model weights and cannot process audio. It exists to prove the build compiles. See packaging/windows/README.md.
        REM model-licenses is staged per-model, so it is empty here too, and an
        REM empty directory is not reliably covered by skipifsourcedoesntexist.
        > "%STAGE%\model-licenses\NO-MODELS-IN-THIS-BUILD.txt" echo No models were bundled, so no model licenses apply to this build.
    ) else (
        echo error: no redistributable models found in %MODEL_SOURCE_DIR% >&2
        echo        run scripts\import_models.py --source ..\Music-Assassin\models first, >&2
        echo        or pass a source dir: scripts\build_windows.bat C:\path\to\models >&2
        echo        ^(CI only: set ALLOW_NO_MODELS=1 for a compile check^) >&2
        exit /b 1
    )
)

set "VBCABLE_ARG="
if exist "%WIN_PKG_DIR%\vendor\VBCABLE_Setup_x64.exe" (
    echo ==^> VB-CABLE installer found in packaging\windows\vendor\ — will bundle
    set "VBCABLE_ARG=/DBundleVBCable=1"
) else (
    echo ==^> no VB-CABLE installer in packaging\windows\vendor\ — building fallback installer
    echo      ^(app will prompt the user to install it manually on first run^)
)

where ISCC.exe >nul 2>nul
if errorlevel 1 (
    if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
        set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    ) else (
        echo error: ISCC.exe ^(Inno Setup compiler^) not found on PATH or in the default install dir. >&2
        echo        Install Inno Setup 6.4.3 ^(see packaging\windows\README.md^) and re-run. >&2
        exit /b 1
    )
) else (
    set "ISCC=ISCC.exe"
)

if not exist "%DIST_DIR%" mkdir "%DIST_DIR%"

echo ==^> compiling installer
"%ISCC%" /DMyAppVersion=%VERSION% /DStageDir="%STAGE%" /DExeDir="%BUILD_DIR%\pyinstaller\dist" /DOutputDir="%DIST_DIR%" %VBCABLE_ARG% "%WIN_PKG_DIR%\music-assassin-live.iss"
if errorlevel 1 (
    echo Inno Setup compilation failed.
    exit /b 1
)

popd
echo ==^> done: dist\music-assassin-live-setup-%VERSION%.exe
exit /b 0

:stage_model
if not exist "%MODEL_SOURCE_DIR%\%~1" (
    echo     skip: %~1 not found in %MODEL_SOURCE_DIR%
    exit /b 0
)
copy /y "%MODEL_SOURCE_DIR%\%~1" "%STAGE%\models\%~1" >nul
copy /y "%ROOT%\models\%~2" "%STAGE%\models\%~2" >nul
copy /y "%WIN_PKG_DIR%\..\model-licenses\%~3" "%STAGE%\model-licenses\%~3" >nul
set "BUNDLED_ANY=1"
echo     bundled: %~1
exit /b 0

:stage_dtln
if not exist "%MODEL_SOURCE_DIR%\dtln_model_1.onnx" exit /b 0
if not exist "%MODEL_SOURCE_DIR%\dtln_model_2.onnx" exit /b 0
copy /y "%MODEL_SOURCE_DIR%\dtln_model_1.onnx" "%STAGE%\models\dtln_model_1.onnx" >nul
copy /y "%MODEL_SOURCE_DIR%\dtln_model_2.onnx" "%STAGE%\models\dtln_model_2.onnx" >nul
copy /y "%ROOT%\models\dtln.json" "%STAGE%\models\dtln.json" >nul
copy /y "%WIN_PKG_DIR%\..\model-licenses\MIT-dtln.txt" "%STAGE%\model-licenses\MIT-dtln.txt" >nul
set "BUNDLED_ANY=1"
echo     bundled: dtln_model_1.onnx + dtln_model_2.onnx
exit /b 0
