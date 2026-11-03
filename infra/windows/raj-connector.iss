; RAJ Connector - Windows installer (Inno Setup 6)
; Build (after build-connector.ps1 produced dist\raj-connector.exe):
;   iscc /DAppVersion=0.1.0 infra\windows\raj-connector.iss      ->  dist\RAJ-Connector-Setup-0.1.0.exe
; Silent install / pairing (IT rollout):
;   RAJ-Connector-Setup-0.1.0.exe /VERYSILENT /SERVER=https://raj.example.in /CODE=RAJ-XXXX-XXXX-XXXX-XXXX
; A silent install without /CODE only installs; pair later from the Start menu.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6E1B6C2E-6F3A-4F7B-9B0E-5A0F3C1D2E71}
AppName=RAJ Connector
AppVersion={#AppVersion}
AppVerName=RAJ Connector {#AppVersion}
AppPublisher=RAJ Finance Agent
DefaultDirName={autopf}\RAJ Connector
DefaultGroupName=RAJ Connector
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\..\dist
OutputBaseFilename=RAJ-Connector-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\raj-connector.exe
UninstallDisplayName=RAJ Connector
SetupLogging=yes
CloseApplications=no
#ifdef SignTool
SignTool={#SignTool}
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\dist\raj-connector.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "pair.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "README-connector.txt"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{commonappdata}\RAJ"

[Icons]
Name: "{group}\Pair this PC with the firm"; Filename: "{app}\pair.cmd"; WorkingDir: "{app}"
Name: "{group}\Connector status"; Filename: "{cmd}"; Parameters: "/k """"{app}\raj-connector.exe"" --data ""{commonappdata}\RAJ"" status"""
Name: "{group}\Pause connector"; Filename: "{cmd}"; Parameters: "/k """"{app}\raj-connector.exe"" --data ""{commonappdata}\RAJ"" pause"""
Name: "{group}\Resume connector"; Filename: "{cmd}"; Parameters: "/k """"{app}\raj-connector.exe"" --data ""{commonappdata}\RAJ"" resume"""
Name: "{group}\Allow posting on this PC"; Filename: "{cmd}"; Parameters: "/k """"{app}\raj-connector.exe"" --data ""{commonappdata}\RAJ"" enable-writes"""
Name: "{group}\Block posting on this PC"; Filename: "{cmd}"; Parameters: "/k """"{app}\raj-connector.exe"" --data ""{commonappdata}\RAJ"" disable-writes"""
Name: "{group}\Open connector log"; Filename: "{commonappdata}\RAJ\connector.log"
Name: "{group}\Read me"; Filename: "{app}\README-connector.txt"
Name: "{group}\Uninstall RAJ Connector"; Filename: "{uninstallexe}"

[Run]
Filename: "{sys}\icacls.exe"; Parameters: """{commonappdata}\RAJ"" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F /grant:r *S-1-5-32-544:(OI)(CI)F"; Flags: runhidden waituntilterminated; StatusMsg: "Securing the connector's data folder..."
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""if (-not (Get-Command ssh-keygen -ErrorAction SilentlyContinue)) {{ Add-WindowsCapability -Online -Name 'OpenSSH.Client~~~~0.0.1.0' | Out-Null }"""; Flags: runhidden waituntilterminated; StatusMsg: "Checking the Windows OpenSSH client..."; Check: NeedsOpenSsh
Filename: "{app}\pair.cmd"; Parameters: """{code:GetServer}"" ""{code:GetCode}"" ""{code:GetTally}"""; WorkingDir: "{app}"; Flags: waituntilterminated; StatusMsg: "Pairing this PC with the firm..."; Check: ShouldPair
Filename: "{app}\README-connector.txt"; Description: "Read the quick guide"; Flags: postinstall shellexec skipifsilent unchecked

[UninstallRun]
Filename: "{app}\raj-connector.exe"; Parameters: "service uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveTask"

[Code]
var
  PairPage: TInputQueryWizardPage;

procedure InitializeWizard;
begin
  PairPage := CreateInputQueryPage(wpSelectDir,
    'Connect to your CA firm',
    'Enter the details your firm gave you.',
    'TallyPrime must be open with the client''s company, with its port 9000 enabled ' +
    '(F1 Help > Settings > Connectivity). Leave the pairing code empty to pair later from the ' +
    'Start menu ("Pair this PC with the firm"). Posting to Tally stays OFF until both the firm ' +
    'and this PC enable it.');
  PairPage.Add('Firm server address (for example https://raj.example.in):', False);
  PairPage.Add('Pairing code (RAJ-XXXX-XXXX-XXXX-XXXX):', False);
  PairPage.Add('Tally address:', False);
  PairPage.Values[0] := ExpandConstant('{param:SERVER|}');
  PairPage.Values[1] := ExpandConstant('{param:CODE|}');
  PairPage.Values[2] := ExpandConstant('{param:TALLY|http://127.0.0.1:9000}');
end;

function GetServer(Param: String): String;
begin
  Result := Trim(PairPage.Values[0]);
end;

function GetCode(Param: String): String;
begin
  Result := Uppercase(Trim(PairPage.Values[1]));
end;

function GetTally(Param: String): String;
begin
  Result := Trim(PairPage.Values[2]);
  if Result = '' then
    Result := 'http://127.0.0.1:9000';
end;

function ShouldPair: Boolean;
begin
  Result := (GetServer('') <> '') and (GetCode('') <> '');
end;

function NeedsOpenSsh: Boolean;
begin
  Result := not FileExists(ExpandConstant('{sys}\OpenSSH\ssh-keygen.exe'));
end;

function ServerLooksValid(S: String): Boolean;
var
  L: String;
begin
  L := Lowercase(S);
  Result := (Pos('https://', L) = 1) or (Pos('http://127.0.0.1', L) = 1) or (Pos('http://localhost', L) = 1);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = PairPage.ID) and (GetCode('') <> '') then
  begin
    if not ServerLooksValid(GetServer('')) then
    begin
      MsgBox('The firm server address must start with https:// (for example https://raj.example.in).', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if SuppressibleMsgBox('Also delete this PC''s pairing keys and local posting journal in ' +
        ExpandConstant('{commonappdata}\RAJ') + '?' + #13#10#13#10 +
        'Choose No if you may reinstall. Either way, ask the firm to revoke this device.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
      DelTree(ExpandConstant('{commonappdata}\RAJ'), True, True, True);
  end;
end;
