@echo off
:: Dong goi thanh 1 file viettam-core-hub.exe (khong can cai Python tren may chay).
:: Ban chinh thuc duoc build tu dong bang GitHub Actions (.github/workflows/viettam-core-hub.yml).
cd /d "%~dp0"
py -3 -m pip install --upgrade pyinstaller -r requirements.txt || exit /b 1
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin --name viettam-core-hub ^
    --icon assets\icon.ico --version-file version_info.txt --add-data "web;web" app.py || exit /b 1
echo.
echo Xong: dist\viettam-core-hub.exe
pause
