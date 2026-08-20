; Inno Setup script for Music Assassin Live (Windows).
; Built by scripts\build_windows.bat, which passes:
;   MyAppVersion, StageDir (models), ExeDir (PyInstaller output), OutputDir
;   and, when packaging\windows\vendor\VBCABLE_Setup_x64.exe is present,
;   BundleVBCable=1.
;
; NOTE ON SCOPE: this installer gets the binary, models, and the VB-CABLE
; virtual-audio driver onto the machine — it does not by itself make the app
; work on Windows. The routing seam now exists (backends/base.py defines the
; RoutingBackend protocol), but the Windows implementation of it does not:
; trap = CABLE Input, capture = CABLE Output, default-device switching via
; pycaw. See ROADMAP D3.3. This script is packaging scaffolding ahead of
; that, not a claim that Windows support is complete.
;
; WASAPI loopback is NOT the mechanism, despite what earlier notes said. It
; taps the signal where this app has to insert into it — the original audio
; would keep playing underneath the processed one — and sounddevice 0.5.5
; exposes no loopback flag in any case. CABLE Output is a real capture
; endpoint that an ordinary input stream reads.
;
; NOTE ON VB-CABLE: the public build should DOWNLOAD it at install time
; against a pinned SHA-256 (Inno Setup's CreateDownloadPage gives progress
; and takes the hash as a parameter), not bundle it. That ships none of
; VB-Audio's bytes, so nothing is redistributed and their >10-unit threshold
; is never engaged — and it removes five of the seven manual steps. See
; ROADMAP D3.4. That page is NOT written yet; what follows below is still
; the old prompt-the-user fallback, which survives as the offline path.
;
; BundleVBCable=1 remains for internal builds only. Publishing one is
; redistribution whatever the file is named, and is appropriate only once
; vb-audio-permission-email.md has been sent and answered.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif
#ifndef StageDir
  #define StageDir "..\..\build\win-stage"
#endif
#ifndef ExeDir
  #define ExeDir "..\..\build\pyinstaller\dist"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

#define MyAppName "Music Assassin Live"
#define MyAppPublisher "A-Ahmad-02"
#define MyAppURL "https://github.com/A-Ahmad-02/music-assassin-live"
#define MyAppExeName "music-assassin-live.exe"

[Setup]
; Fixed GUID — do not regenerate; Inno Setup uses this to recognise
; upgrades/uninstalls across versions.
AppId={{4F2B6C1A-8E1B-4B7B-9C2E-6C1F6D6B9A11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\Music Assassin Live
DefaultGroupName=Music Assassin Live
LicenseFile=..\..\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=music-assassin-live-setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; Admin needed for Program Files + (when bundled) the VB-CABLE driver install.
PrivilegesRequired=admin
DisableWelcomePage=no
; Generate this once from assassin_live/ui/assets/icon.png, e.g.:
;   magick icon.png -define icon:auto-resize=256,128,64,48,32,16 music-assassin-live.ico
SetupIconFile=..\icons\music-assassin-live.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#ExeDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\models\*"; DestDir: "{app}\models"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\model-licenses\*"; DestDir: "{app}\model-licenses"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
#ifdef BundleVBCable
Source: "vendor\VBCABLE_Setup_x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
#endif

[Icons]
Name: "{group}\Music Assassin Live"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall Music Assassin Live"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Music Assassin Live"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Lets assassin_live/paths.py find the bundled models with zero code changes
; (models_dir() already checks MUSIC_ASSASSIN_MODELS first).
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "MUSIC_ASSASSIN_MODELS"; ValueData: "{app}\models"; Flags: preservestringtype

[Run]
#ifdef BundleVBCable
; VB-CABLE's own installer supports "-i -h" for silent install, but Windows
; still shows one unavoidable driver-signing security prompt — that's the
; OS, not this script; see packaging/windows/README.md.
Filename: "{tmp}\VBCABLE_Setup_x64.exe"; Parameters: "-i -h"; StatusMsg: "Installing VB-CABLE virtual audio driver..."; Flags: waituntilterminated
#endif
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Music Assassin Live"; Flags: nowait postinstall skipifsilent

[Code]
const
  { Prefixed because Inno Setup predefines some of these itself -- an
    unprefixed HWND_BROADCAST aborted the very first compile this script was
    ever put through ("Duplicate identifier"). Which names are built in
    varies by version, so rather than track that, none of ours share a
    namespace with it. }
  MA_WM_SETTINGCHANGE = $001A;
  MA_HWND_BROADCAST = $FFFF;
  MA_SMTO_ABORTIFHUNG = $0002;

function SendMessageTimeoutA(hWnd: Integer; Msg: Integer; wParam: Integer;
  lParam: String; fuFlags: Integer; uTimeout: Integer;
  var lpdwResult: Integer): Integer;
  external 'SendMessageTimeoutA@user32.dll stdcall';

procedure RefreshEnvironment;
var
  ResultCode: Integer;
begin
  { Broadcast WM_SETTINGCHANGE so a freshly-launched app (e.g. the one this
    installer just offered to run) sees MUSIC_ASSASSIN_MODELS without the
    user needing to log off/on. }
  SendMessageTimeoutA(MA_HWND_BROADCAST, MA_WM_SETTINGCHANGE, 0, 'Environment',
    MA_SMTO_ABORTIFHUNG, 5000, ResultCode);
end;

function VBCableAppearsInstalled: Boolean;
begin
  { Heuristic only — used to decide whether to prompt, never to skip a step
    that would otherwise run. VB-CABLE registers itself here under both its
    32-bit and 64-bit install paths. }
  Result := RegKeyExists(HKLM32, 'SOFTWARE\VB-Audio\Cable') or
            RegKeyExists(HKLM64, 'SOFTWARE\VB-Audio\Cable');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrorCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
#ifndef BundleVBCable
    if not VBCableAppearsInstalled then
    begin
      if MsgBox('Music Assassin Live needs VB-CABLE, a free virtual audio ' +
                'driver, to route system sound through it on Windows. ' +
                'Open the VB-CABLE download page now?',
                mbConfirmation, MB_YESNO) = IDYES then
        ShellExecAsOriginalUser('open', 'https://vb-audio.com/Cable/', '', '',
          SW_SHOWNORMAL, ewNoWait, ErrorCode);
    end;
#endif
    RefreshEnvironment;
  end;
end;
