@echo off
setlocal
cd /d "%~dp0"
title VietTam Core Hub

:: Chay tu ma nguon Python. Cach don gian hon: dung viettam-core-hub.exe (trang Releases).
:: LibreHardwareMonitor can quyen Administrator de doc nhiet do CPU -> tu nang quyen 1 lan.
net session >nul 2>&1
if errorlevel 1 (
    if "%~1"=="" (
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    ) else (
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    )
    exit /b
)

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY ( python -c "import sys" >nul 2>&1 && set "PY=python" )
if not defined PY (
    echo Chua co Python 3.9+. Cai bang lenh: winget install -e --id Python.Python.3.12
    echo Hoac dung ban viettam-core-hub.exe khong can Python.
    pause & exit /b 1
)

:: Pillow can cho overlay 3 dong ho noi (chi cai lan dau)
%PY% -c "import PIL" >nul 2>&1 || %PY% -m pip install --user -r requirements.txt

:: app.py tu tai, cau hinh va khoi dong LibreHardwareMonitor
%PY% app.py %*
if errorlevel 1 pause
