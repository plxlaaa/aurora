; Aurora Windows 安装器（Inno Setup 6）
; 编译：ISCC /DAPP_VERSION=<版本> installer.iss（CI 由 tag 推导传入）
; 布局：{localappdata}\Aurora\{backend\{app,site-packages}, frontend\dist, config, runtime, data}
; 选 LOCALAPPDATA（PrivilegesRequired=lowest）：免管理员、应用内在线更新可直接写入

; 版本：优先取环境变量 APP_VERSION（CI 由 tag 推导，无 v 前缀），缺省 1.6.0
#define AppVersion GetEnv("APP_VERSION")
#ifndef AppVersion
  #define AppVersion "1.6.2"
#endif

#define RepoRoot GetEnv("GITHUB_WORKSPACE")
#ifndef RepoRoot
  #define RepoRoot "..\.."
#endif

[Setup]
AppId={{8F4D2A61-52E7-4B3A-9C8D-1A2B3C4D5E6F}}
AppName=Aurora
AppVersion={#AppVersion}
AppPublisher=plxlaaa
DefaultDirName={localappdata}\Aurora
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=AuroraSetup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=Aurora AI 助手
LicenseFile={#RepoRoot}\installer\windows\LICENSE.txt

[Languages]
Name: "chs"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; Flags: unchecked

[Files]
Source: "{#RepoRoot}\stage\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Dirs]
Name: "{app}\data"

[Icons]
; 主入口直指 pythonw（无控制台黑框）；排障用 aurora.bat
Name: "{group}\Aurora"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m app.desktop"; WorkingDir: "{app}\backend"; Comment: "Aurora AI 助手（桌面窗口）"
Name: "{group}\Aurora（浏览器模式）"; Filename: "{app}\aurora-web.bat"; Comment: "浏览器访问 http://127.0.0.1:8080"
Name: "{group}\Aurora（排障控制台）"; Filename: "{app}\aurora.bat"; Comment: "带控制台输出启动，用于排查启动问题"
Name: "{group}\停止 Aurora"; Filename: "{app}\aurora-stop.bat"; Comment: "停止本地 Aurora 进程"
Name: "{group}\卸载 Aurora"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Aurora"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m app.desktop"; WorkingDir: "{app}\backend"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m app.desktop"; WorkingDir: "{app}\backend"; Description: "立即启动 Aurora"; Flags: nowait postinstall skipifsilent

[Code]
// 卸载流程：确认卸载 → 停进程 → 询问是否保留用户数据（默认保留）
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  pidFile, cmd: String;
  resultCode: Integer;
  pidLines: TArrayOfString;
begin
  if CurUninstallStep = usUninstall then
  begin
    // ① 第一问：确定卸载吗？（否 → 中止）
    if MsgBox('确定卸载 Aurora 吗？', mbConfirmation, MB_YESNO) = IDNO then
      Abort;

    // ② 停止后台进程（读 PID 文件精确终止，不误伤其他进程）
    pidFile := ExpandConstant('{app}\data\aurora.pid');
    if FileExists(pidFile) then
    begin
      if LoadStringsFromFile(pidFile, pidLines) and (GetArrayLength(pidLines) > 0) then
      begin
        if Trim(pidLines[0]) <> '' then
        begin
          cmd := '/C taskkill /F /T /PID ' + Trim(pidLines[0]);
          Exec(ExpandConstant('{cmd}'), cmd, '', SW_HIDE, ewWaitUntilTerminated, resultCode);
        end;
      end;
    end;

    // ③ 第二问：是否保留用户数据？（默认是=保留；明确选否才删除）
    if MsgBox('是否保留用户数据（会话历史、本地配置）？' #13#10 #13#10
              '「是」= 保留（推荐）。数据将保留在安装目录，可随时手动删除。' #13#10
              '「否」= 连同 data 与 config 一并删除，不可恢复。',
              mbConfirmation, MB_YESNO) = IDNO then
    begin
      DelTree(ExpandConstant('{app}\data'), True, True, True);
      DelTree(ExpandConstant('{app}\config'), True, True, True);
      DelTree(ExpandConstant('{app}\logs'), True, True, True);
    end;
    // 选择保留时：Inno 仅删除安装时记录的程序文件，运行期生成的 data/、*.local.yaml 不会被动到
  end;
end;
