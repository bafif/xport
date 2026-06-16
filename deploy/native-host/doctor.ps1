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

Write-Host "[4] backend: en WSL + alcanzable desde Windows"
# OJO (NAT): el forwarding de WSL2 reenvia IPv4 127.0.0.1, pero 'localhost' en Windows
# resuelve a ::1 (IPv6) primero y PowerShell NO hace fallback -> SIEMPRE 127.0.0.1.
# Primero confirmamos el backend DENTRO de WSL; asi distinguimos "caido" de "forwarding roto".
$inWsl = (wsl.exe @WslPre -- bash -c "curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://127.0.0.1:8000/gate" 2>$null) -join ""
$wslUp = ($inWsl -match '200')
if ($wslUp) { Ok "backend vivo dentro de WSL (127.0.0.1:8000 -> 200)" }
else { Inf "backend no responde dentro de WSL (http='$inWsl') - normal si no cargaste la extension / no hay server" }

$winOk = $false
try {
  $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:8000/gate"
  if ($r.StatusCode -eq 200 -and $r.Content -match 'hard_cap') { $winOk = $true }
} catch { }
if ($winOk) {
  Ok "alcanzable desde Windows por 127.0.0.1:8000 (asi lo alcanza la extension)"
} elseif ($wslUp) {
  No "el backend corre en WSL pero Windows NO lo alcanza por 127.0.0.1:8000 (forwarding NAT)"
  Wn "fix: en %UserProfile%\.wslconfig -> [wsl2] / localhostForwarding=true, luego 'wsl --shutdown'. O networkingMode=mirrored."
  $ipraw = (wsl.exe @WslPre -- bash -c "hostname -I" 2>$null) -join " "
  $ip = ($ipraw.Trim() -split '\s+')[0]
  if ($ip) { Inf "fallback directo (inestable): http://${ip}:8000  (requiere bind 0.0.0.0; la IP de WSL cambia en cada reinicio)" }
} else {
  Inf "nada en :8000 - carga la extension (auto-arranca el backend) o levanta uvicorn a mano y reintenta"
}

Write-Host ""
if     ($script:fail -gt 0) { Write-Host "RESULT: FAIL ($script:fail) · WARN ($script:warn)" -ForegroundColor Red }
elseif ($script:warn -gt 0) { Write-Host "RESULT: OK con avisos · WARN ($script:warn)" -ForegroundColor Yellow }
else   { Write-Host "RESULT: OK" -ForegroundColor Green }
