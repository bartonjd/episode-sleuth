<#
.SYNOPSIS
    Build a signed .msix installer for the DVD Episode Identifier (Windows x64).

.DESCRIPTION
    Two stages:
      1. PyInstaller bundles the Fluent GUI (audio_fingerprint.gui package,
         plus config.json and the Vosk model) into a self-contained folder app
         under dist\.
      2. MakeAppx packs that folder into an .msix using packaging\AppxManifest.xml,
         and SignTool signs it with a self-signed certificate so it can be
         installed locally (side-loaded).

    The resulting .msix does NOT need Python installed on the target machine.
    FFmpeg is bundled automatically if it is found on PATH at build time
    (recommended: run install.ps1 first so it exists).

.PARAMETER Version
    Package version, must be 4 parts (e.g. 1.0.0.0). Default 1.0.0.0.

.PARAMETER CertPassword
    Password for the generated signing certificate .pfx. Default "dvdid".

.PARAMETER SkipBundleTools
    Do not try to copy ffmpeg.exe into the package.

.PARAMETER Publisher
    Certificate subject / manifest Publisher. Default "CN=DVDIdentifier".
    If you change this, it is written into the manifest automatically.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\build_msix.ps1 -Version 1.0.0.0

.NOTES
    Prerequisites (build machine only):
      * Python 3 on PATH
      * Windows 10/11 SDK  (provides makeappx.exe and signtool.exe)
        Install with:  winget install Microsoft.WindowsSDK.10.0.22621
    PyInstaller is installed automatically into a build venv if missing.
#>
[CmdletBinding()]
param(
    [string]$Version = "1.0.0.0",
    [string]$CertPassword = "dvdid",
    [switch]$SkipBundleTools,
    [string]$Publisher = "CN=DVDIdentifier"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ProjectDir

function Info($m) { Write-Host "[*] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[!] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[X] $m" -ForegroundColor Red }

if ($Version -notmatch '^\d+\.\d+\.\d+\.\d+$') {
    throw "Version must have 4 numeric parts, e.g. 1.0.0.0 (got '$Version')."
}

$AppName   = "dvd_identifier_fluent"
$DistApp   = Join-Path $ProjectDir "dist\$AppName"
$Assets    = Join-Path $ProjectDir "packaging\assets"
$Manifest  = Join-Path $ProjectDir "packaging\AppxManifest.xml"
$Icon      = Join-Path $ProjectDir "packaging\app.ico"
$OutMsix   = Join-Path $ProjectDir "DVDEpisodeIdentifier_$Version.msix"
$Pfx       = Join-Path $ProjectDir "packaging\dvdid_selfsign.pfx"

foreach ($p in @($Manifest, $Assets)) {
    if (-not (Test-Path $p)) {
        Fail "Missing $p"
        Write-Host "  Run:  python packaging\make_icons.py   (to (re)generate assets)"
        throw "missing packaging inputs"
    }
}
if (-not (Test-Path $Icon)) {
    Info "app.ico missing - generating icons ..."
    python (Join-Path $ProjectDir "packaging\make_icons.py") | Out-Host
}

# ---------------------------------------------------------------------------
# 0. Locate Windows SDK tools (makeappx / signtool)
# ---------------------------------------------------------------------------
function Find-SdkTool($exe) {
    $c = Get-Command $exe -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $roots = @("${env:ProgramFiles(x86)}\Windows Kits\10\bin",
               "${env:ProgramFiles}\Windows Kits\10\bin")
    foreach ($root in $roots) {
        if (Test-Path $root) {
            $hit = Get-ChildItem -Path $root -Recurse -Filter $exe -ErrorAction SilentlyContinue |
                   Where-Object { $_.FullName -match "x64" } |
                   Sort-Object FullName -Descending | Select-Object -First 1
            if ($hit) { return $hit.FullName }
        }
    }
    return $null
}

$MakeAppx = Find-SdkTool "makeappx.exe"
$SignTool = Find-SdkTool "signtool.exe"
if (-not $MakeAppx -or -not $SignTool) {
    Fail "Could not find makeappx.exe / signtool.exe (Windows 10/11 SDK)."
    Write-Host "  Install the SDK, e.g.:"
    Write-Host "    winget install Microsoft.WindowsSDK.10.0.22621"
    Write-Host "  then re-run this script."
    throw "Windows SDK tools missing"
}
Ok "makeappx: $MakeAppx"
Ok "signtool: $SignTool"

# ---------------------------------------------------------------------------
# 1. Build the app with PyInstaller (in an isolated build venv)
# ---------------------------------------------------------------------------
$buildVenv = Join-Path $ProjectDir ".buildvenv"
if (-not (Test-Path $buildVenv)) {
    Info "Creating build venv (.buildvenv) ..."
    python -m venv $buildVenv
}
$bpy = Join-Path $buildVenv "Scripts\python.exe"
Info "Installing build + runtime deps into build venv ..."
& $bpy -m pip install --upgrade pip | Out-Host
& $bpy -m pip install pyinstaller | Out-Host
& $bpy -m pip install -r (Join-Path $ProjectDir "requirements.txt") | Out-Host

Info "Running PyInstaller ..."
$modelsPath = Join-Path $ProjectDir "models"
$addModels = ""
if (Test-Path $modelsPath) { $addModels = "$modelsPath;models" }

# Clean previous output so stale files are never packed.
if (Test-Path (Join-Path $ProjectDir "dist"))  { Remove-Item -Recurse -Force (Join-Path $ProjectDir "dist") }
if (Test-Path (Join-Path $ProjectDir "build")) { Remove-Item -Recurse -Force (Join-Path $ProjectDir "build") }

$piArgs = @(
    "--noconfirm", "--clean", "--windowed",
    "--name", $AppName,
    "--icon", $Icon,
    "--add-data", "$((Join-Path $ProjectDir 'config.json'));.",
    "--collect-all", "vosk",
    "--collect-all", "qfluentwidgets",
    "--collect-submodules", "metaphone",
    "--collect-submodules", "audio_fingerprint",
    "--paths", (Split-Path $ProjectDir -Parent)
)
if ($addModels) { $piArgs += @("--add-data", $addModels) }
$piArgs += (Join-Path $ProjectDir "gui\__main__.py")

& $bpy -m PyInstaller @piArgs | Out-Host
if (-not (Test-Path (Join-Path $DistApp "$AppName.exe"))) {
    throw "PyInstaller did not produce $DistApp\$AppName.exe"
}
Ok "PyInstaller build complete: $DistApp"

# ---------------------------------------------------------------------------
# 2. Stage packaging inputs into the dist app folder
# ---------------------------------------------------------------------------
Info "Staging manifest + assets ..."
Copy-Item -Recurse -Force $Assets (Join-Path $DistApp "assets")

# Write the manifest with the version + publisher substituted in.
$mx = Get-Content $Manifest -Raw
$mx = $mx.Replace("{VERSION}", $Version)
$mx = $mx -replace 'Publisher="CN=DVDIdentifier"', ('Publisher="' + $Publisher + '"')
Set-Content -Path (Join-Path $DistApp "AppxManifest.xml") -Value $mx -Encoding UTF8
Ok "Manifest staged (Version=$Version, Publisher=$Publisher)."

# Bundle ffmpeg if present so the app is self-contained.
if (-not $SkipBundleTools) {
    foreach ($tool in @("ffmpeg.exe", "ffprobe.exe")) {
        $src = (Get-Command $tool -ErrorAction SilentlyContinue).Source
        if ($src) {
            Copy-Item -Force $src (Join-Path $DistApp $tool)
            Ok "Bundled $tool"
        } else {
            Warn "$tool not found on PATH - not bundled (users will need it installed)."
        }
    }
}

# ---------------------------------------------------------------------------
# 3. Pack into .msix
# ---------------------------------------------------------------------------
Info "Packing MSIX ..."
if (Test-Path $OutMsix) { Remove-Item -Force $OutMsix }
& $MakeAppx pack /d $DistApp /p $OutMsix /o | Out-Host
if (-not (Test-Path $OutMsix)) { throw "MakeAppx failed to produce $OutMsix" }
Ok "Created $OutMsix"

# ---------------------------------------------------------------------------
# 4. Self-signed certificate + sign
# ---------------------------------------------------------------------------
Info "Preparing signing certificate ..."
$cert = Get-ChildItem Cert:\CurrentUser\My -ErrorAction SilentlyContinue |
        Where-Object { $_.Subject -eq $Publisher } | Select-Object -First 1
if (-not $cert) {
    Info "Creating self-signed cert $Publisher ..."
    $cert = New-SelfSignedCertificate -Type Custom -Subject $Publisher `
        -KeyUsage DigitalSignature -FriendlyName "DVD Identifier self-sign" `
        -CertStoreLocation "Cert:\CurrentUser\My" `
        -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3",
                         "2.5.29.19={text}")
}
$secPw = ConvertTo-SecureString -String $CertPassword -Force -AsPlainText
Export-PfxCertificate -Cert $cert -FilePath $Pfx -Password $secPw | Out-Null
# Also export the public .cer so users can trust it before installing.
$cer = Join-Path $ProjectDir "packaging\dvdid_selfsign.cer"
Export-Certificate -Cert $cert -FilePath $cer | Out-Null
Ok "Certificate ready (.pfx + .cer in packaging\)."

Info "Signing MSIX ..."
& $SignTool sign /fd SHA256 /a /f $Pfx /p $CertPassword $OutMsix | Out-Host
Ok "Signed $OutMsix"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "======================================================================"
Write-Host "  MSIX build complete"
Write-Host "======================================================================"
Write-Host "  Package : $OutMsix"
Write-Host "  Cert    : $cer  (public cert to trust)"
Write-Host ""
Write-Host "To install on THIS or another machine (self-signed => trust the cert once):"
Write-Host "  1. Import the certificate into 'Trusted People' (run as admin):"
Write-Host "       Import-Certificate -FilePath `"$cer`" \"
Write-Host "         -CertStoreLocation Cert:\LocalMachine\TrustedPeople"
Write-Host "  2. Double-click $([System.IO.Path]::GetFileName($OutMsix)) and click Install,"
Write-Host "     or:  Add-AppxPackage -Path `"$OutMsix`""
Write-Host ""
Write-Host "  (A properly code-signed cert from a CA would skip step 1 for end users.)"
