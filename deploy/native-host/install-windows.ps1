<#
.SYNOPSIS
  Instala el native-messaging host de xport para Firefox/Chrome corriendo en WINDOWS,
  con el backend (FastAPI) adentro de WSL. El navegador lanza un .bat que hace
  `wsl.exe -- bash xport-host.sh`, y wsl.exe bridgea stdin/stdout (el canal nativo)
  hasta el supervisor Linux. Resultado: abrir la extensión arranca uvicorn en WSL solo.

.NOTES
  Corré esto desde PowerShell EN WINDOWS (no dentro de WSL). Una sola vez.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install-windows.ps1
  powershell -ExecutionPolicy Bypass -File install-windows.ps1 -Distro Ubuntu -RepoPath /home/bafif/xport
  powershell -ExecutionPolicy Bypass -File install-windows.ps1 -Chrome -ExtId <chrome-id>
  powershell -ExecutionPolicy Bypass -File install-windows.ps1 -Uninstall
#>
[CmdletBinding()]
param(
  [string]$RepoPath = "/home/bafif/xport",   # ruta del repo DENTRO de WSL
  [string]$Distro   = "",                     # distro WSL; vacío = la default
  [string]$ExtId    = "xport@local",          # Firefox: gecko id | Chrome: extension id
  [switch]$Chrome,
  [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$HostName = "com.xport.host"
$Dest     = Join-Path $env:LOCALAPPDATA "xport"
$Manifest = Join-Path $Dest "$HostName.json"
$Bat      = Join-Path $Dest "xport-host.bat"
$RegBase  = if ($Chrome) { "HKCU:\Software\Google\Chrome\NativeMessagingHosts" }
            else         { "HKCU:\Software\Mozilla\NativeMessagingHosts" }
$RegKey   = Join-Path $RegBase $HostName

if ($Uninstall) {
  Remove-Item -Path $RegKey -Recurse -ErrorAction SilentlyContinue
  Remove-Item -Path $Manifest, $Bat -ErrorAction SilentlyContinue
  Write-Host "x  desinstalado: registro + $Manifest"
  return
}

New-Item -ItemType Directory -Force -Path $Dest | Out-Null

# El .bat es lo que lanza el navegador (proceso Windows). Bridgea a WSL con wsl.exe.
# `bash <script>` no necesita +x; el script arregla el PATH para encontrar uv.
$DistroArg = if ($Distro) { "-d $Distro " } else { "" }
$Script    = "$RepoPath/deploy/native-host/xport-host.sh"
$BatBody   = "@echo off`r`nwsl.exe $DistroArg-- bash `"$Script`""
Set-Content -Path $Bat -Value $BatBody -Encoding Ascii

# El manifest apunta al .bat. En JSON los backslashes de la ruta Windows van escapados.
$PathJson = ($Bat -replace '\\', '\\')
$Allowed  = if ($Chrome) { "`"allowed_origins`": [`"chrome-extension://$ExtId/`"]" }
            else         { "`"allowed_extensions`": [`"$ExtId`"]" }
$Json = @"
{
  "name": "$HostName",
  "description": "xport backend launcher (supervisor del FastAPI local)",
  "path": "$PathJson",
  "type": "stdio",
  $Allowed
}
"@
Set-Content -Path $Manifest -Value $Json -Encoding Ascii

# La clave de registro (valor por default) apunta al manifest: así lo encuentra el navegador.
New-Item -Path $RegKey -Force | Out-Null
Set-ItemProperty -Path $RegKey -Name "(Default)" -Value $Manifest

Write-Host "✓ instalado ($(if ($Chrome) {'Chrome'} else {'Firefox'})):"
Write-Host "    manifest: $Manifest"
Write-Host "    bat:      $Bat"
Write-Host "    bridge:   wsl.exe $DistroArg-- bash $Script"
Write-Host "    registro: $RegKey"
Write-Host "    ext id:   $ExtId"
Write-Host ""
Write-Host "Siguiente: recargá la extension en el navegador. Al abrirla arranca el backend en WSL."
