; Инсталлятор AionMeter. Сборка:
;     py build.py            — сначала соберёт dist/AionMeter
;     py installer/make.py   — потом соберёт installer/out/AionMeterSetup-<версия>.exe
;
; Нужен Inno Setup 6: https://jrsoftware.org/isdl.php
;
; Почему обычный инсталлятор, а не MSI и не Store: MSI требует возни с
; таблицами ради того же результата, а в Store неподписанное приложение не
; попадёт. Inno даёт ярлык, запись в «Программы и компоненты» и корректное
; удаление — этого достаточно.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\AionMeter"
#endif

#define MyAppName "AionMeter"
#define MyAppExe "AionMeter.exe"
#define MyAppPublisher "AionMeter"
#define MyAppURL "https://github.com/Steepeek/AionMeter"

[Setup]
AppId={{7C4B1F2E-9A3D-4E58-9C71-6F2A5D8B3E14}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases

; Ставим в профиль пользователя, а не в Program Files. Причина простая:
; программа пишет настройки и лог рядом с собой только в крайнем случае, но
; установка без прав администратора убирает окно UAC — а для неподписанного
; приложения лишнее окно с предупреждением это лишний повод не поставить.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=no

OutputDir=out
OutputBaseFilename=AionMeterSetup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
UninstallDisplayIcon={app}\{#MyAppExe}
SetupIconFile=..\docs\aionmeter.ico
LicenseFile=..\LICENSE

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Запускать вместе с Windows"; \
    GroupDescription: "Дополнительно:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExe}"""; \
    Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Настройки и лог живут в %APPDATA% и остаются: человек может ставить
; заново, и терять при этом разложенное окно и горячие клавиши обидно.
; Удалять их вручную — {userappdata}\AionMeter
Type: filesandordirs; Name: "{app}\assets"
