@echo off
setlocal
pushd "%~dp0" || exit /b 1
uv run --locked python database\init_local_db.py %*
set "exit_code=%errorlevel%"
popd
exit /b %exit_code%
