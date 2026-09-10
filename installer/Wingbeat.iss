; Инсталлятор Wingbeat. Сборка:
;     py build.py            — сначала соберёт dist/Wingbeat
;     py installer/make.py   — потом соберёт installer/out/WingbeatSetup-<версия>.exe
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
  #define SourceDir "..\dist\Wingbeat"
#endif

#define MyAppName "Wingbeat"
#define MyAppExe "Wingbeat.exe"
#define MyAppPublisher "Wingbeat"
#define MyAppURL "https://github.com/Steepeek/Wingbeat"

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
OutputBaseFilename=WingbeatSetup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
UninstallDisplayIcon={app}\{#MyAppExe}
SetupIconFile=..\docs\wingbeat.ico
LicenseFile=..\LICENSE

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Запускать вместе с Windows"; \
    GroupDescription: "Дополнительно:"; Flags: unchecked

[InstallDelete]
; AppId остался прежним, поэтому установка ложится поверх AionMeter как
; обновление. Файлы старой версии при этом не исчезают сами: без уборки
; рядом с Wingbeat.exe остался бы работающий AionMeter.exe, и человек
; продолжал бы запускать старую программу, не понимая, почему ничего не
; изменилось.
Type: files; Name: "{app}\AionMeter.exe"
Type: files; Name: "{group}\AionMeter.lnk"
Type: files; Name: "{group}\Удалить AionMeter.lnk"
Type: files; Name: "{autodesktop}\AionMeter.lnk"

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
; Автозапуск прежней версии указывает на удалённый AionMeter.exe. Если
; его не убрать, Windows при каждом входе будет пытаться запустить то,
; чего уже нет.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueName: "AionMeter"; Flags: deletevalue uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Настройки и лог живут в %APPDATA% и остаются: человек может ставить
; заново, и терять при этом разложенное окно и горячие клавиши обидно.
; Удалять их вручную — {userappdata}\Wingbeat
Type: filesandordirs; Name: "{app}\assets"
