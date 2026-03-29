@echo off
echo.
echo [31mDecisionsAI Uninstaller[0m
echo ================================
echo.
echo This will remove:
echo   - DecisionsAI application files
echo   - Python virtual environment
echo   - Start Menu shortcuts
echo   - Desktop shortcut
echo   - PATH entry
echo.
set /p CONFIRM="Are you sure? (y/N): "
if /i not "%CONFIRM%"=="y" (
    echo Cancelled.
    pause
    exit /b 0
)
echo.
echo [33mStopping DecisionsAI...[0m
taskkill /F /IM pythonw.exe 2>nul
taskkill /F /IM python.exe 2>nul
timeout /t 2 /nobreak >nul
echo [33mRemoving shortcuts...[0m
rmdir /s /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\DecisionsAI" 2>nul
del /f "%USERPROFILE%\Desktop\DecisionsAI.lnk" 2>nul
echo [33mRemoving virtual environment...[0m
rmdir /s /q "%USERPROFILE%\.virtualenvs\decisions" 2>nul
echo [33mRemoving application files...[0m
set "INSTALL_DIR=%~dp0.."
rmdir /s /q "%INSTALL_DIR%" 2>nul
echo.
echo [32mDecisionsAI has been uninstalled.[0m
echo.
pause
