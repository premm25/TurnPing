; Inno Setup script for TurnPing-<version>-Setup.exe (built by scripts/build.ps1).
;
; Per-user install: no administrator rights, files go to %LOCALAPPDATA%\Programs\TurnPing\app
; (the same layout as scripts/install.ps1, so both kinds of install upgrade each other).
; Agent hooks and Windows registration are done by `turnping-cli install`, which backs up
; and preserves every agent settings file it edits. Uninstall runs `turnping-cli uninstall`.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "TurnPing"
#define DistDir "..\dist\TurnPing"

[Setup]
AppId={{4ED149D2-7BD0-4DAF-BDAF-877815BD6B97}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=TurnPing contributors
AppPublisherURL=https://github.com/premm25/TurnPing
AppSupportURL=https://github.com/premm25/TurnPing/issues
AppUpdatesURL=https://github.com/premm25/TurnPing/releases
DefaultDirName={localappdata}\Programs\TurnPing
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile=..\LICENSE
SetupIconFile=..\src\turnping\desktop\assets\turnping.ico
UninstallDisplayIcon={app}\app\TurnPing.exe
UninstallDisplayName={#AppName}
OutputDir=..\dist
OutputBaseFilename=TurnPing-{#AppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "connectagents"; Description: "Connect the AI coding agents found on this PC (Claude Code, Codex CLI, Gemini CLI)"
Name: "startup"; Description: "Start TurnPing when I sign in to Windows"
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[InstallDelete]
; Remove files from an older version so stale libraries never mix with new ones.
Type: filesandordirs; Name: "{app}\app"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs

[UninstallDelete]
; Also covers a folder first created by scripts/install.ps1, which Setup doesn't own.
Type: dirifempty; Name: "{app}"

[Icons]
Name: "{userprograms}\TurnPing"; Filename: "{app}\app\TurnPing.exe"; Parameters: "run"; Comment: "Desktop notifications for AI coding agents"
Name: "{userdesktop}\TurnPing"; Filename: "{app}\app\TurnPing.exe"; Parameters: "run"; Tasks: desktopicon

[Run]
Filename: "{app}\app\TurnPing.exe"; Parameters: "run"; Description: "Open TurnPing now"; Flags: postinstall nowait skipifsilent

[Code]
function CliPath(): String;
begin
  Result := ExpandConstant('{app}\app\turnping-cli.exe');
end;

function RunCli(const Params: String): Integer;
var
  Code: Integer;
begin
  if not Exec(CliPath(), Params, '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Code := -1;
  Result := Code;
end;

procedure StopRunningApp();
begin
  if FileExists(CliPath()) then
  begin
    RunCli('quit');
    Sleep(1500);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningApp();  { an upgrade must not replace files under a running app }
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Params: String;
begin
  if CurStep = ssPostInstall then
  begin
    Params := 'install';
    if not WizardIsTaskSelected('connectagents') then
      Params := Params + ' --no-hooks';
    if WizardIsTaskSelected('startup') then
      Params := Params + ' --startup';
    if RunCli(Params) <> 0 then
      SuppressibleMsgBox('TurnPing was installed, but connecting your coding agents reported a problem.' + #13#10 + #13#10 +
                         'Open TurnPing: the dashboard shows each integration''s status and an Install button.',
                         mbInformation, MB_OK, IDOK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Params: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    StopRunningApp();
    Params := 'uninstall';
    if SuppressibleMsgBox('Also delete your TurnPing history and preferences?' + #13#10 + #13#10 +
                          'Backups of your agent settings files are kept either way.',
                          mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
      Params := Params + ' --purge-data';
    { removes only TurnPing's hooks and registry entries; other settings stay untouched }
    RunCli(Params);
  end;
end;
