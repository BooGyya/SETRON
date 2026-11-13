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
if not "%~1"=="" goto run_arguments

echo 이 도구는 setron.env가 지정한 Windows MySQL DB만 관리합니다.
echo.
echo 1. 시연 데이터 넣기
echo 2. 데이터 초기화 - 테이블 구조 유지, 모든 데이터 삭제
echo 0. 종료
echo.
choice /c 120 /n /m "선택 [1/2/0]: "
if errorlevel 3 goto cancelled
if errorlevel 2 goto reset_data
if errorlevel 1 goto import_demo
goto cancelled

:import_demo
set "db_action=--import-demo"
goto run_action

:reset_data
set "db_action=--reset-data"

:run_action
echo.
echo 진행 전 확인창이 나타나며, 기존 데이터는 먼저 backups에 백업합니다.
echo 아니오 또는 취소를 선택하면 기존 데이터가 유지됩니다.
pushd "%~dp0"
"%initializer%" %db_action%
goto result

:run_arguments
pushd "%~dp0"
"%initializer%" %*

:result
set "exit_code=%errorlevel%"
popd
echo.
if "%exit_code%"=="0" echo 완료: 선택한 작업과 행 수 검증을 마쳤습니다.
if "%exit_code%"=="3" echo 취소: 기존 테이블과 데이터를 변경하지 않았습니다.
if not "%exit_code%"=="0" if not "%exit_code%"=="3" echo 중단: 화면의 오류와 백업 경로를 확인하세요.
pause
exit /b %exit_code%

:cancelled
echo 종료: DB를 변경하지 않았습니다.
exit /b 0
