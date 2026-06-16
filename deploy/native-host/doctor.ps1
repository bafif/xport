<#
.SYNOPSIS
  Doctor del native host — lado WINDOWS: verifica registro + manifest + el bridge
  .bat→WSL + que el backend sea alcanzable desde Windows. Para el lado WSL (que el
  backend pueda arrancar) corré doctor.sh en WSL.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File doctor.ps1
  powershell -ExecutionPolicy Bypass -File doctor.ps1 -Distro Ubuntu
  powershell -ExecutionPolicy Bypass -File doctor.ps1 -Chrome
#>
[CmdletBinding()]
param([switch]$Chrome, [string]$Distro = "")

$ErrorActionPreference = "Continue"
$HostName = "com.xport.host"
$RegBase  = if ($Chrome) { "HKCU:\Software\Google\Chrome\NativeMessagingHosts" }
            else         { "HKCU:\Software\Mozilla\NativeMessagingHosts" }
$RegKey   = Join-Path $RegBase $HostName
$script:fail = 0; $script:warn = 0
function Ok ($m) { Write-Host "  [OK] $m" -ForegroundColor Green }
function No ($m) { Write-Host "  [X]  $m" -ForegroundColor Red;    $script:fail++ }
function Wn ($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow; $script:warn++ }
function Inf($m) { Write-Host "  [i]  $m" -ForegroundColor Cyan }

$WslPre = @(); if ($Distro) { $WslPre = @('-d', $Distro) }

Write-Host "xport native-host doctor (Windows)  ·  $(if ($Chrome) {'Chrome'} else {'Firefox'})"
Write-Host ""

Write-Host "[1] registro"
$ManifestPath = $null
if (Test-Path $RegKey) {
  $ManifestPath = (Get-Item $RegKey).GetValue("")   # valor (default) de la clave
  if ($ManifestPath) { Ok "clave $HostName -> $ManifestPath" }
  else { No "la clave existe pero no tiene valor (default)" }
} else {
  No "falta la clave $RegKey — corré install-windows.ps1"
}

Write-Host "[2] manifest"
$BatPath = $null
if ($ManifestPath -and (Test-Path $ManifestPath)) {
  try {
    $m = Get-Content $ManifestPath -Raw | ConvertFrom-Json
    Ok "manifest JSON válido ($ManifestPath)"
    if ($m.name -ne $HostName) { No "name=$($m.name) (esperaba $HostName)" }
    $BatPath = $m.path
    if ($BatPath -and (Test-Path $BatPath)) { Ok "path -> $BatPath (existe)" }
    else { No "path -> $BatPath (NO existe)" }
    $allowed = if ($Chrome) { $m.allowed_origins } else { $m.allowed_extensions }
    Inf "autorizado: $($allowed -join ', ')"
  } catch { No "manifest no es JSON válido: $_" }
} elseif ($ManifestPath) {
  No "el registro apunta a $ManifestPath pero el archivo no existe"
}

Write-Host "[3] bridge .bat -> WSL"
if ($BatPath -and (Test-Path $BatPath)) {
  $bat = (Get-Content $BatPath -Raw).Trim()
  Inf "contenido: $bat"
  $mScript = [regex]::Match($bat, 'bash\s+"?([^"]+xport-host\.sh)"?')
  if ($mScript.Success) {
    $script = $mScript.Groups[1].Value
    $probe = (wsl.exe @WslPre -- bash -c "if [ -x '$script' ]; then echo XOK; elif [ -f '$script' ]; then echo NOEXEC; else echo MISSING; fi" 2>$null) -join ""
    if     ($probe -match 'XOK')    { Ok "WSL ve el wrapper ejecutable: $script" }
    elseif ($probe -match 'NOEXEC') { Wn "el wrapper existe en WSL pero no es +x: $script  (chmod +x)" }
    elseif ($probe -match 'MISSING'){ No "WSL no encuentra el wrapper: $script  (¿-Distro / RepoPath correctos?)" }
    else   { Wn "no pude verificar el wrapper vía wsl.exe (probe='$probe')" }
    $uv = (wsl.exe @WslPre -- bash -lc "command -v uv || echo NONE" 2>$null) -join ""
    if ($uv -and $uv -notmatch 'NONE') { Ok "uv en WSL: $uv" }
    else { Wn "no veo uv en el PATH de login de WSL (el wrapper igual prueba ~/.local/bin)" }
  } else { No "el .bat no invoca xport-host.sh" }
} else {
  Inf "salteo (no hay .bat válido del paso anterior)"
}

Write-Host "[4] backend alcanzable desde Windows (forwarding WSL2)"
try {
  $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://localhost:8000/gate"
  if ($r.StatusCode -eq 200 -and $r.Content -match 'hard_cap') { Ok "xport vivo y alcanzable: $($r.Content)" }
  else { Wn "responde en :8000 pero no parece xport" }
} catch {
  Inf "nada en localhost:8000 (normal si Firefox no está abierto; al cargar la extensión debería levantar)"
}

Write-Host ""
if     ($script:fail -gt 0) { Write-Host "RESULT: FAIL ($script:fail) · WARN ($script:warn)" -ForegroundColor Red }
elseif ($script:warn -gt 0) { Write-Host "RESULT: OK con avisos · WARN ($script:warn)" -ForegroundColor Yellow }
else   { Write-Host "RESULT: OK" -ForegroundColor Green }
