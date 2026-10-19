@echo off
:: Dong goi thanh 1 file viettam-core-hub.exe (khong can cai Python tren may chay).
:: Ban chinh thuc duoc build tu dong bang GitHub Actions (.github/workflows/viettam-core-hub.yml).
cd /d "%~dp0"
:: Python tu python.org co lenh "py"; ban Microsoft Store chi co "python"
set "PY=py -3"
where py >nul 2>nul || set "PY=python"
%PY% -m pip install --upgrade pyinstaller -r requirements.txt || (echo Loi: khong cai duoc PyInstaller/Pillow & pause & exit /b 1)
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin --name viettam-core-hub ^
    --icon assets\icon.ico --version-file version_info.txt --add-data "web;web" app.py || (echo Loi: dong goi that bai & pause & exit /b 1)
echo.
echo Xong: dist\viettam-core-hub.exe
pause
