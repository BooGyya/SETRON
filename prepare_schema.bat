@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "initializer=%~dp0dist\SETRON\SETRON_DB_SETUP.exe"
if not exist "%initializer%" set "initializer=%~dp0SETRON_DB_SETUP.exe"
if not exist "%initializer%" (
    echo SETRON_DB_SETUP.exe was not found. Extract the complete package before running this file.
    pause
    exit /b 1
)
pushd "%~dp0"
if errorlevel 1 (
    echo Could not open the package folder.
    pause
    exit /b 1
)
"%initializer%" %*
set "exit_code=%errorlevel%"
popd
if "%~1"=="" pause
exit /b %exit_code%
