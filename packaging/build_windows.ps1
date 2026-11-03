# Windows 실행 파일 폴더를 만든다 (Jira S15P31B106-77). 저장소 최상위 폴더에서 실행한다.
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
# 결과: dist\SETRON\SETRON.exe 와 설정 예시 setron.env.example
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    uv sync --locked
    uv run --locked pyinstaller packaging\setron.spec --noconfirm --distpath dist --workpath build\pyinstaller
    Copy-Item .env.windows.example dist\SETRON\setron.env.example -Force
    Write-Host "완료: dist\SETRON\SETRON.exe (setron.env.example을 setron.env로 복사해 접속 정보를 입력하세요)"
}
finally {
    Pop-Location
}
