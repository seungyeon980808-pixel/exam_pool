#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\ExamPool-HWP-Converter"
#endif
#ifndef InstallerOutputDir
  #define InstallerOutputDir "..\..\dist\installer"
#endif

[Setup]
AppId={{A607B1B4-0989-43A0-A683-2B95B333F29A}
AppName=ExamPool HWP 변환기
AppVersion={#MyAppVersion}
AppPublisher=ExamPool
DefaultDirName={localappdata}\Programs\ExamPool HWP 변환기
DefaultGroupName=ExamPool HWP 변환기
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#InstallerOutputDir}
OutputBaseFilename=ExamPool-HWP-Converter-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayName=ExamPool HWP 변환기
LicenseFile=..\..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "바탕 화면에 바로가기 만들기"; GroupDescription: "추가 바로가기:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ExamPool HWP 변환기"; Filename: "{app}\ExamPoolHwpConverter.exe"
Name: "{autodesktop}\ExamPool HWP 변환기"; Filename: "{app}\ExamPoolHwpConverter.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ExamPoolHwpConverter.exe"; Description: "ExamPool HWP 변환기 실행"; Flags: nowait postinstall skipifsilent
