@echo off
title MediaGrabber - Build EXE
echo ============================================
echo   Building MediaGrabber.exe
echo ============================================
echo.

:: Set working directory to where this .bat lives
cd /d "%~dp0"
set "APPDIR=%cd%"

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found on PATH.
    echo Install Python from python.org and ensure "Add to PATH" is checked.
    pause
    exit /b 1
)

:: Install PyInstaller
echo Installing PyInstaller...
python -m pip install pyinstaller --quiet
if errorlevel 1 (
    echo ERROR: Failed to install PyInstaller.
    pause
    exit /b 1
)

:: Build
echo.
echo Compiling MediaGrabber.exe...
python -m PyInstaller --onefile --console --name MediaGrabber --clean --noconfirm --collect-submodules mediagrabber --distpath "%APPDIR%" "%APPDIR%\mediagrabber.py"
if errorlevel 1 (
    echo ERROR: Build failed.
    pause
    exit /b 1
)

:: Cleanup build artifacts
echo.
echo Cleaning up build files...
if exist "%APPDIR%\build" rmdir /s /q "%APPDIR%\build"
if exist "%APPDIR%\__pycache__" rmdir /s /q "%APPDIR%\__pycache__"
if exist "%APPDIR%\MediaGrabber.spec" del /q "%APPDIR%\MediaGrabber.spec"

echo.
echo ============================================
echo   BUILD COMPLETE!
echo   MediaGrabber.exe is ready in this folder.
echo ============================================
echo.
pause
