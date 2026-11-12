@echo off
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "initializer=%~dp0dist\SETRON_Docker_DB\SETRON_DOCKER_DB_INIT.exe"
if not exist "%initializer%" set "initializer=%~dp0SETRON_DOCKER_DB_INIT.exe"
if not exist "%initializer%" (
    echo SETRON_DOCKER_DB_INIT.exe를 찾지 못했습니다. Docker 시연 배포 폴더 전체를 압축 해제하세요.
    pause
    exit /b 1
)
echo Docker MySQL에 시연 데이터를 넣습니다. Windows MySQL 설정은 사용하지 않습니다.
echo 기본 컨테이너: setron-mysql-dev-1. 다른 이름은 --container 이름 으로 지정하세요.
echo 기존 관리 데이터가 있으면 확인을 묻고, y를 입력한 경우에만 SQL 백업 후 교체합니다.
echo.
pushd "%~dp0"
"%initializer%" %*
set "exit_code=%errorlevel%"
popd
echo.
if "%exit_code%"=="0" echo 완료: Docker 시연 자료와 설비, 관측, 라인별 행 수를 확인했습니다.
if "%exit_code%"=="3" echo 취소: 기존 Docker 테이블과 데이터를 변경하지 않았습니다.
if not "%exit_code%"=="0" if not "%exit_code%"=="3" echo 중단: 위의 오류와 백업 경로를 확인하세요.
pause
exit /b %exit_code%
