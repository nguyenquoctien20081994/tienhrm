@echo off
:: Dong goi thanh 1 file ThermalCoreHUD.exe (khong can cai Python tren may chay).
:: Van can thu muc lhm\ (LibreHardwareMonitor) dat canh file exe.
cd /d "%~dp0"
py -3 -m pip install --upgrade pyinstaller || exit /b 1
py -3 -m PyInstaller --noconfirm --onefile --name ThermalCoreHUD --add-data "web;web" app.py || exit /b 1
echo.
echo Xong: dist\ThermalCoreHUD.exe
pause
