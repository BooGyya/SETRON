@echo off
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "initializer=%~dp0dist\SETRON\SETRON_DB_INIT.exe"
if not exist "%initializer%" set "initializer=%~dp0SETRON_DB_INIT.exe"
if not exist "%initializer%" (
    echo SETRON_DB_INIT.exe를 찾지 못했습니다. 배포 폴더 전체를 압축 해제한 뒤 실행하세요.
    pause
    exit /b 1
)
rem 메뉴, 확인 질문, 성공/실패 안내는 EXE에서 한 번만 표시합니다.
pushd "%~dp0"
"%initializer%" %*
set "exit_code=%errorlevel%"
popd
exit /b %exit_code%
