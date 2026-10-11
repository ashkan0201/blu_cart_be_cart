@echo off
REM Run from the project root (next to app.py). Build with Python 3.8 (64-bit).
setlocal
py -3.8 --version
if errorlevel 1 (
  echo Python 3.8 not found.
  pause
  exit /b 1
)

py -3.8 -m venv .buildenv
if errorlevel 1 goto err
call .buildenv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install flask==3.0.3 selenium==4.25.0 pyinstaller==6.10.0
if errorlevel 1 goto err

pyinstaller --noconfirm --clean BluTransfer.spec
if errorlevel 1 goto err

REM Only drivers are copied next to the exe (your database / username / password are NOT copied)
xcopy /E /I /Y drivers dist\BluTransfer\drivers >nul

echo.
echo Done. Output: dist\BluTransfer
pause
exit /b 0

:err
echo Build failed.
pause
exit /b 1
