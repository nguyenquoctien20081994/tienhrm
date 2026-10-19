# Thermal Core HUD - cai dat cho Windows 10/11
# 1) Tai LibreHardwareMonitor (ban co dinh, kiem tra SHA256)  2) Bat web server noi bo 127.0.0.1:8085
# 3) Kiem tra Python (tu cai bang winget neu chua co)
param([switch]$Force)
$ErrorActionPreference = 'Stop'

$Root       = Split-Path -Parent $MyInvocation.MyCommand.Path
$LhmDir     = Join-Path $Root 'lhm'
$LhmExe     = Join-Path $LhmDir 'LibreHardwareMonitor.exe'
$LhmVersion = 'v0.9.6'
$LhmUrl     = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/$LhmVersion/LibreHardwareMonitor.zip"
$LhmSha256  = '086D9F1B5A99E643EDC2CFAAAC16051685B551E4C5AC0B32A57C58C0E529C001'

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

# ---------------------------------------------------------------- 1. LibreHardwareMonitor
$running = Get-Process -Name LibreHardwareMonitor -ErrorAction SilentlyContinue
if ($running) {
    # LHM ghi de file cau hinh khi thoat, nen phai dung no truoc khi sua cau hinh
    Step 'Dung LibreHardwareMonitor dang chay de cap nhat cau hinh...'
    $running | Stop-Process -Force
    Start-Sleep -Seconds 1
}

if ($Force -or -not (Test-Path $LhmExe)) {
    Step "Tai LibreHardwareMonitor $LhmVersion ..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $zip = Join-Path $env:TEMP "LibreHardwareMonitor-$LhmVersion.zip"
    Invoke-WebRequest -Uri $LhmUrl -OutFile $zip -UseBasicParsing
    $hash = (Get-FileHash -Path $zip -Algorithm SHA256).Hash
    if ($hash -ne $LhmSha256) {
        Remove-Item $zip -Force
        throw "SHA256 khong khop ($hash). Dung cai dat de dam bao an toan."
    }
    New-Item -ItemType Directory -Force -Path $LhmDir | Out-Null
    Expand-Archive -Path $zip -DestinationPath $LhmDir -Force
    Get-ChildItem -Path $LhmDir -Recurse -File | Unblock-File
    Remove-Item $zip -Force
    Step "Da giai nen vao $LhmDir"
}

# ---------------------------------------------------------------- 2. Cau hinh LHM
$cfgPath = Join-Path $LhmDir 'LibreHardwareMonitor.config'
$xml = New-Object System.Xml.XmlDocument
if (Test-Path $cfgPath) { $xml.Load($cfgPath) }
else { $xml.LoadXml('<?xml version="1.0" encoding="utf-8"?><configuration><appSettings /></configuration>') }
$app = $xml.SelectSingleNode('/configuration/appSettings')
if (-not $app) {
    $app = $xml.CreateElement('appSettings')
    [void]$xml.SelectSingleNode('/configuration').AppendChild($app)
}
$settings = [ordered]@{
    runWebServerMenuItem  = 'true'       # bat Remote Web Server
    listenerIp            = '127.0.0.1'  # chi may nay truy cap duoc, khong lo ra mang LAN
    listenerPort          = '8085'
    authenticationEnabled = 'false'
    startMinMenuItem      = 'true'       # khoi dong thu nho
    minTrayMenuItem       = 'true'       # thu nho xuong khay he thong
    minCloseMenuItem      = 'true'       # bam X = thu nho, khong tat
}
foreach ($key in $settings.Keys) {
    $node = $app.SelectSingleNode("add[@key='$key']")
    if (-not $node) {
        $node = $xml.CreateElement('add')
        $node.SetAttribute('key', $key)
        [void]$app.AppendChild($node)
    }
    $node.SetAttribute('value', $settings[$key])
}
$xml.Save($cfgPath)
Step 'Da bat LibreHardwareMonitor web server tai http://127.0.0.1:8085'

# ---------------------------------------------------------------- 3. Python
function Find-Python {
    foreach ($cand in @(@('py', '-3'), @('python'))) {
        $exe = $cand[0]; $rest = @($cand | Select-Object -Skip 1)
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            try {
                & $exe @rest -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
                if ($LASTEXITCODE -eq 0) { return $cand -join ' ' }
            } catch { }
        }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Step 'Chua co Python 3.9+. Cai Python 3.12 bang winget (chi cho tai khoan hien tai)...'
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $py = Find-Python
    if (-not $py) { throw 'Khong tim thay Python sau khi cai. Hay cai thu cong tu https://www.python.org/downloads/ roi chay lai.' }
}
Step "Python: $py"
Step 'Cai dat xong.'
