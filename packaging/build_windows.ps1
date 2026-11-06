# Windows 실행 파일 폴더를 만든다 (Jira S15P31B106-77). 저장소 최상위 폴더에서 실행한다.
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
# 결과: 루트의 setron.exe 와 dist\SETRON 실행 파일 폴더
$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).ProviderPath
$uv = (Get-Command uv -CommandType Application).Source
$compiler = Join-Path $env:SystemRoot "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$dist = Join-Path $root "dist"
$bundle = Join-Path $dist "SETRON"
$launcher = Join-Path $root "setron.exe"
$stage = [IO.Path]::GetFullPath((Join-Path $dist ("setron-stage-" + [guid]::NewGuid().ToString("N"))))
$stagedBundle = Join-Path $stage "SETRON"
$stagedLauncher = Join-Path $stage "setron.exe"
$previousBundle = Join-Path $stage "previous-SETRON"
$previousLauncher = Join-Path $stage "previous-setron.exe"
$originalPath = $env:Path
$stageCreated = $false
$keepStage = $false
$publishLock = $null

function Move-PackageDirectory($source, $destination) {
    for ($attempt = 0; ; $attempt++) {
        try {
            [IO.Directory]::Move($source, $destination)
            return
        }
        catch {
            # Windows에서 잠시 잠긴 새 EXE나 DLL 때문에 디렉터리 이름 변경이 지연될 수 있다.
            $code = $_.Exception.GetBaseException().HResult -band 65535
            if ($attempt -ge 19 -or $code -notin 5, 32, 33 -or
                    -not (Test-Path -LiteralPath $source -PathType Container) -or
                    (Test-Path -LiteralPath $destination)) {
                throw
            }
            Start-Sleep -Milliseconds 500
        }
    }
}

Push-Location -LiteralPath $root
try {
    if ((Split-Path -Parent $dist) -ne $root -or (Split-Path -Parent $stage) -ne $dist) {
        throw "패키지 출력 경로를 확인해 주세요."
    }
    [IO.Directory]::CreateDirectory($dist) | Out-Null
    if ((Get-Item -LiteralPath $dist -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "dist는 이 저장소의 일반 폴더여야 합니다."
    }
    New-Item -ItemType Directory -Path $stage | Out-Null
    $stageCreated = $true

    & $uv sync --locked
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync 실패 (종료 코드: $LASTEXITCODE)"
    }
    # PyInstaller가 다른 프로그램의 PATH에서 Qt·Windows DLL을 가져오지 않도록 한다.
    $env:Path = "$env:SystemRoot\System32;$env:SystemRoot"
    & $uv run --locked --no-sync pyinstaller packaging\setron.spec --noconfirm --distpath $stage --workpath (Join-Path $stage "work")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 빌드 실패 (종료 코드: $LASTEXITCODE)"
    }
    Copy-Item -LiteralPath .env.windows.example -Destination (Join-Path $stagedBundle "setron.env.example")
    & $compiler /nologo /target:winexe /codepage:65001 /reference:System.Windows.Forms.dll "/out:$stagedLauncher" packaging\run_setron_windows.cs
    if ($LASTEXITCODE -ne 0) {
        throw "루트 실행 파일 빌드 실패 (종료 코드: $LASTEXITCODE)"
    }

    # 동시에 빌드하더라도 한 번에 하나의 작업만 배포 폴더를 교체한다.
    $publishLock = [IO.FileStream]::new((Join-Path $dist ".setron-publish.lock"),
        [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None, 4096,
        [IO.FileOptions]::DeleteOnClose)
    $exePaths = @($launcher, (Join-Path $bundle "SETRON.exe"))
    $running = Get-Process -Name SETRON -ErrorAction SilentlyContinue | Where-Object {
        -not $_.Path -or $_.Path -in $exePaths
    }
    if ($running) {
        throw "실행 중인 SETRON을 닫은 후 다시 빌드해 주세요."
    }

    $existingBundle = Get-Item -LiteralPath $bundle -Force -ErrorAction SilentlyContinue
    if ($existingBundle -and (-not $existingBundle.PSIsContainer -or
            ($existingBundle.Attributes -band [IO.FileAttributes]::ReparsePoint))) {
        throw "dist\SETRON은 이 저장소의 일반 폴더여야 합니다."
    }
    $bundlePublished = $false
    try {
        if ($existingBundle) {
            Move-PackageDirectory $bundle $previousBundle
            $settings = Join-Path $previousBundle "setron.env"
            if (Test-Path -LiteralPath $settings -PathType Leaf) {
                Copy-Item -LiteralPath $settings -Destination $stagedBundle
            }
        }
        Move-PackageDirectory $stagedBundle $bundle
        $bundlePublished = $true
        if ([IO.File]::Exists($launcher)) {
            [IO.File]::Replace($stagedLauncher, $launcher, $previousLauncher)
        }
        else {
            [IO.File]::Move($stagedLauncher, $launcher)
        }
    }
    catch {
        try {
            if ($bundlePublished) {
                Move-PackageDirectory $bundle $stagedBundle
            }
            if ([IO.Directory]::Exists($previousBundle)) {
                Move-PackageDirectory $previousBundle $bundle
            }
            if ([IO.File]::Exists($previousLauncher)) {
                if ([IO.File]::Exists($launcher)) {
                    [IO.File]::Replace($previousLauncher, $launcher, $stagedLauncher)
                }
                else {
                    [IO.File]::Move($previousLauncher, $launcher)
                }
            }
        }
        catch {
            $keepStage = $true
            throw "이전 패키지를 복원하지 못했습니다. 복구 파일을 보존했습니다. $stage"
        }
        throw
    }
    Write-Host "완료: setron.exe (설정 파일은 dist\SETRON\setron.env)"
}
finally {
    $env:Path = $originalPath
    Pop-Location
    if ($publishLock) {
        $publishLock.Dispose()
    }
    if ($stageCreated -and -not $keepStage) {
        $stageDirectory = Get-Item -LiteralPath $stage -Force -ErrorAction SilentlyContinue
        if ($stageDirectory -and $stageDirectory.FullName -eq $stage -and
                -not ($stageDirectory.Attributes -band [IO.FileAttributes]::ReparsePoint) -and
                -not ((Get-Item -LiteralPath $dist -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction Continue
        }
    }
}
