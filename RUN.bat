@echo off
title MediaGrabber
cd /d "%~dp0"

:: Try the compiled exe first, fall back to Python
if exist "%~dp0MediaGrabber.exe" (
    "%~dp0MediaGrabber.exe"
) else (
    python "%~dp0mediagrabber.py"
)
