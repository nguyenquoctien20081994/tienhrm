@echo off
setlocal
cd /d "%~dp0"
title Thermal Core HUD

:: LibreHardwareMonitor can quyen Administrator de doc nhiet do CPU,
:: va ACPI (nhiet do mainboard/EC) cung can quyen nay -> tu nang quyen 1 lan.
net session >nul 2>&1
if errorlevel 1 (
    if "%~1"=="" (
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    ) else (
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    )
    exit /b
)

if not exist "lhm\LibreHardwareMonitor.exe" (
    echo [Thermal Core] Lan dau chay: dang cai dat...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-windows.ps1"
    if errorlevel 1 ( echo Cai dat that bai. & pause & exit /b 1 )
)

tasklist /fi "imagename eq LibreHardwareMonitor.exe" | find /i "LibreHardwareMonitor.exe" >nul
if errorlevel 1 (
    echo [Thermal Core] Khoi dong LibreHardwareMonitor...
    start "" /min "lhm\LibreHardwareMonitor.exe"
    timeout /t 4 /nobreak >nul
)

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY ( python -c "import sys" >nul 2>&1 && set "PY=python" )
if not defined PY (
    echo Chua co Python. Chay: powershell -ExecutionPolicy Bypass -File setup-windows.ps1
    pause & exit /b 1
)

%PY% app.py %*
if errorlevel 1 pause
