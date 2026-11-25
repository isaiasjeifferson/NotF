; Instalador do NotF (Inno Setup 6)
#define Versao GetEnv("NOTARA_VERSAO")
#if Versao == ""
  #error Defina NOTARA_VERSAO (ex.: set NOTARA_VERSAO=1.0.0)
#endif

[Setup]
AppId={{6C3E1B52-8E0B-4E9B-9C55-0B1F2A9D7A11}
AppName=NotF
AppVersion={#Versao}
AppPublisher=NotF
VersionInfoVersion={#Versao}
DefaultDirName={localappdata}\Programs\NotF
UsePreviousAppDir=no
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=yes
DisableWelcomePage=yes
DisableFinishedPage=yes
WizardSmallImageFile=logo-pequeno.bmp,logo-pequeno-2x.bmp
WizardSizePercent=100
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=NotF-{#Versao}-Instalar
SetupIconFile=..\notara\icone.ico
UninstallDisplayIcon={app}\NotF.exe
UninstallDisplayName=NotF
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force

[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Files]
Source: "..\build\dist\NotF\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: PrecisaWebView2

[InstallDelete]
; versões antigas se chamavam Notara
Type: filesandordirs; Name: "{localappdata}\Programs\Notara"
Type: files; Name: "{userprograms}\Notara.lnk"
Type: files; Name: "{userdesktop}\Notara.lnk"

[Icons]
Name: "{userprograms}\NotF"; Filename: "{app}\NotF.exe"
Name: "{userdesktop}\NotF"; Filename: "{app}\NotF.exe"

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Instalando componente do Windows (WebView2)…"; Check: PrecisaWebView2
Filename: "{app}\NotF.exe"; Flags: nowait

[Messages]
SetupWindowTitle=Instalando o NotF
WizardInstalling=Instalando o NotF
InstallingLabel=
StatusExtractFiles=Copiando arquivos…
StatusCreateIcons=Criando atalhos…
StatusRunProgram=Abrindo o NotF…
StatusClosingApplications=Fechando o NotF…
StatusRestartingApplications=Abrindo o NotF…

[Code]
var Titulo: TNewStaticText;

procedure InitializeWizard;
begin
  { Visual limpo, alinhado ao NotF: fundo branco, sem faixa superior e sem divisórias }
  WizardForm.Color := clWhite;
  WizardForm.InnerPage.Color := clWhite;
  WizardForm.MainPanel.Visible := False;
  WizardForm.Bevel1.Visible := False;
  WizardForm.Bevel.Visible := False;
  WizardForm.Font.Name := 'Segoe UI';

  Titulo := TNewStaticText.Create(WizardForm);
  Titulo.Parent := WizardForm.InstallingPage;
  Titulo.Caption := 'Instalando o NotF';
  Titulo.Font.Name := 'Segoe UI';
  Titulo.Font.Size := 15;
  Titulo.Font.Style := [fsBold];
  Titulo.Font.Color := $000C6AEA;
  Titulo.Top := ScaleY(8);
  Titulo.Left := 0;

  WizardForm.StatusLabel.Top := Titulo.Top + ScaleY(44);
  WizardForm.FilenameLabel.Visible := False;
  WizardForm.ProgressGauge.Top := WizardForm.StatusLabel.Top + ScaleY(26);
end;

const WV2 = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
const WV2B = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function TemVersao(Raiz: Integer; Chave: String): Boolean;
var V: String;
begin
  Result := RegQueryStringValue(Raiz, Chave, 'pv', V) and (V <> '') and (V <> '0.0.0.0');
end;

function PrecisaWebView2: Boolean;
begin
  Result := not (TemVersao(HKLM, WV2) or TemVersao(HKLM, WV2B) or TemVersao(HKCU, WV2B));
end;

procedure CurUninstallStepChanged(Etapa: TUninstallStep);
begin
  if (Etapa = usPostUninstall) and DirExists(ExpandConstant('{userappdata}\NotF')) then
    if SuppressibleMsgBox('Apagar também os certificados e ajustes guardados neste computador?' + #13#10 +
       'As notas emitidas, em Documentos, não serão apagadas.', mbConfirmation, MB_YESNO, IDNO) = IDYES then
      DelTree(ExpandConstant('{userappdata}\NotF'), True, True, True);
end;
