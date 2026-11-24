; Instalador do Notara (Inno Setup 6)
#define Versao GetEnv("NOTARA_VERSAO")
#if Versao == ""
  #error Defina NOTARA_VERSAO (ex.: set NOTARA_VERSAO=1.0.0)
#endif

[Setup]
AppId={{6C3E1B52-8E0B-4E9B-9C55-0B1F2A9D7A11}
AppName=Notara
AppVersion={#Versao}
AppPublisher=Notara
VersionInfoVersion={#Versao}
DefaultDirName={localappdata}\Programs\Notara
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=yes
DisableWelcomePage=yes
DisableFinishedPage=yes
WizardSmallImageFile=logo-pequeno.bmp,logo-pequeno-2x.bmp
WizardSizePercent=100
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Notara-{#Versao}-Instalar
SetupIconFile=..\notara\icone.ico
UninstallDisplayIcon={app}\Notara.exe
UninstallDisplayName=Notara
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force

[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Files]
Source: "..\build\dist\Notara\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: PrecisaWebView2

[Icons]
Name: "{userprograms}\Notara"; Filename: "{app}\Notara.exe"
Name: "{userdesktop}\Notara"; Filename: "{app}\Notara.exe"

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Instalando componente do Windows (WebView2)…"; Check: PrecisaWebView2
Filename: "{app}\Notara.exe"; Flags: nowait

[Messages]
SetupWindowTitle=Instalando o Notara
WizardInstalling=Instalando o Notara
InstallingLabel=
StatusExtractFiles=Copiando arquivos…
StatusCreateIcons=Criando atalhos…
StatusRunProgram=Abrindo o Notara…
StatusClosingApplications=Fechando o Notara…
StatusRestartingApplications=Abrindo o Notara…

[Code]
var Titulo: TNewStaticText;

procedure InitializeWizard;
begin
  { Visual limpo, alinhado ao Notara: fundo branco, sem faixa superior e sem divisórias }
  WizardForm.Color := clWhite;
  WizardForm.InnerPage.Color := clWhite;
  WizardForm.MainPanel.Visible := False;
  WizardForm.Bevel1.Visible := False;
  WizardForm.Bevel.Visible := False;
  WizardForm.Font.Name := 'Segoe UI';

  Titulo := TNewStaticText.Create(WizardForm);
  Titulo.Parent := WizardForm.InstallingPage;
  Titulo.Caption := 'Instalando o Notara';
  Titulo.Font.Name := 'Segoe UI';
  Titulo.Font.Size := 15;
  Titulo.Font.Style := [fsBold];
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
  if (Etapa = usPostUninstall) and DirExists(ExpandConstant('{userappdata}\Notara')) then
    if SuppressibleMsgBox('Apagar também os certificados e ajustes guardados neste computador?' + #13#10 +
       'As notas emitidas, em Documentos, não serão apagadas.', mbConfirmation, MB_YESNO, IDNO) = IDYES then
      DelTree(ExpandConstant('{userappdata}\Notara'), True, True, True);
end;
