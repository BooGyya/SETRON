@echo off
setlocal
set "initializer=%~dp0SETRON_DB_INIT.exe"
if not exist "%initializer%" set "initializer=%~dp0dist\SETRON\SETRON_DB_INIT.exe"
if not exist "%initializer%" (
    echo SETRON_DB_INIT.exe was not found. Build or extract the complete Windows package first.
    exit /b 1
)
pushd "%~dp0" || exit /b 1
"%initializer%" %*
set "exit_code=%errorlevel%"
popd
exit /b %exit_code%
