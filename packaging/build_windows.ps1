# Windows 전체 배포본: GUI와 동일한 시연 DB 메뉴 EXE/BAT를 함께 만든다.
# powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -DumpPath dist\SETRON\SETRON_DB.sql
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$DumpPath,
    [ValidateNotNullOrEmpty()]
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

function Assert-RegularPackagePath([string]$path) {
    $current = [IO.Path]::GetFullPath($path)
    while ($current) {
        $entry = Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
        if ($entry -and ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "배포 경로에는 바로 가기나 연결된 폴더를 사용할 수 없습니다: $current"
        }
        $parent = Split-Path -Parent $current
        if ($parent -eq $current) { break }
        $current = $parent
    }
}

function Assert-RegularPackageTree([string]$path) {
    Assert-RegularPackagePath $path
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($path)
    while ($pending.Count) {
        $entry = Get-Item -LiteralPath $pending.Pop() -Force
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "보존할 배포 파일에는 바로 가기나 연결된 폴더를 사용할 수 없습니다: $($entry.FullName)"
        }
        if ($entry.PSIsContainer) {
            foreach ($child in Get-ChildItem -LiteralPath $entry.FullName -Force) {
                $pending.Push($child.FullName)
            }
        }
    }
}

function Get-PackageFileHash([string]$path) {
    $stream = [IO.File]::OpenRead($path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace("-", "")
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Move-PackageDirectory($source, $destination) {
    for ($attempt = 0; ; $attempt++) {
        try {
            [IO.Directory]::Move($source, $destination)
            return
        }
        catch {
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

function Invoke-WindowsPackageBuild {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$DumpPath,
        [string]$OutputDir = ""
    )
    $root = (Resolve-Path -LiteralPath $RepositoryRoot).ProviderPath
    $dump = (Resolve-Path -LiteralPath $DumpPath).ProviderPath
    if (-not (Test-Path -LiteralPath $dump -PathType Leaf)) {
        throw "-DumpPath에는 배포할 완전한 SETRON SQL 파일을 지정해 주세요."
    }
    Assert-RegularPackagePath $dump
    $bundle = if ($OutputDir) {
        $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDir)
    } else {
        Join-Path $root "dist\SETRON"
    }
    $bundle = [IO.Path]::GetFullPath($bundle).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $parent = Split-Path -Parent $bundle
    if (-not $parent -or $root -eq $bundle -or
            $root.StartsWith($bundle + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "배포 폴더로 저장소나 상위 폴더를 지정할 수 없습니다."
    }
    $rootPrefix = $root.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $distPrefix = (Join-Path $root "dist") + [IO.Path]::DirectorySeparatorChar
    if ($bundle.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase) -and
            -not $bundle.StartsWith($distPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "저장소 내부의 배포 대상은 dist 하위 폴더만 사용할 수 있습니다."
    }
    Assert-RegularPackagePath $bundle
    $uv = @(Get-Command uv -CommandType Application)[0].Source
    $stage = [IO.Path]::GetFullPath((Join-Path $parent ("setron-stage-" + [guid]::NewGuid().ToString("N"))))
    $stagedBundle = Join-Path $stage "SETRON"
    $previousBundle = Join-Path $stage "previous-SETRON"
    $snapshot = Join-Path $stage "requested.dataset.dump"
    $lockPath = Join-Path $parent ".setron-publish.lock"
    $executables = @((Join-Path $bundle "SETRON.exe"), (Join-Path $bundle "SETRON_DB_INIT.exe"))
    $originalPath = $env:Path
    $stageCreated = $false
    $keepStage = $false
    $publishLock = $null
    $previousMoved = $false
    $published = $false
    Push-Location -LiteralPath $root
    try {
        [IO.Directory]::CreateDirectory($parent) | Out-Null
        Assert-RegularPackagePath $parent
        New-Item -ItemType Directory -Path $stage | Out-Null
        $stageCreated = $true
        Copy-Item -LiteralPath $dump -Destination $snapshot
        $requestedHash = Get-PackageFileHash $snapshot

        & $uv sync --locked
        if ($LASTEXITCODE -ne 0) { throw "uv sync 실패 (종료 코드: $LASTEXITCODE)" }
        $env:Path = "$env:SystemRoot\System32;$env:SystemRoot"
        & $uv run --locked --no-sync pyinstaller packaging\setron.spec --noconfirm --distpath $stage --workpath (Join-Path $stage "work")
        if ($LASTEXITCODE -ne 0) { throw "GUI 빌드 실패 (종료 코드: $LASTEXITCODE)" }
        & $uv run --locked --no-sync python packaging\build_demo_db.py --dump $snapshot --output-dir $stagedBundle
        if ($LASTEXITCODE -ne 0) { throw "시연 DB 도구 빌드 실패 (종료 코드: $LASTEXITCODE)" }
        Copy-Item -LiteralPath (Join-Path $root "setron.env.example") -Destination $stagedBundle
        foreach ($name in @("SETRON.exe", "SETRON_DB_INIT.exe", "prepare_demo_db.bat", "SETRON_DB.sql", "setron.env.example")) {
            if (-not (Test-Path -LiteralPath (Join-Path $stagedBundle $name) -PathType Leaf)) {
                throw "새 배포본에 필요한 파일이 없습니다: $name"
            }
        }
        if ((Get-PackageFileHash (Join-Path $stagedBundle "SETRON_DB.sql")) -ne $requestedHash) {
            throw "새 배포본의 SQL이 지정한 dump와 다릅니다. 기존 배포본은 변경하지 않았습니다."
        }

        Assert-RegularPackagePath $lockPath
        $publishLock = [IO.FileStream]::new($lockPath,
            [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None, 4096,
            [IO.FileOptions]::DeleteOnClose)
        $running = Get-Process -Name SETRON, SETRON_DB_INIT -ErrorAction SilentlyContinue | Where-Object {
            -not $_.Path -or $_.Path -in $executables
        }
        if ($running) { throw "실행 중인 SETRON을 닫은 후 다시 빌드해 주세요." }
        Assert-RegularPackagePath $bundle
        $existingBundle = Get-Item -LiteralPath $bundle -Force -ErrorAction SilentlyContinue
        if ($existingBundle -and -not $existingBundle.PSIsContainer) {
            throw "배포 대상은 일반 폴더여야 합니다."
        }
        if ($existingBundle -and @(Get-ChildItem -LiteralPath $bundle -Force).Count -gt 0 -and
                -not (Test-Path -LiteralPath (Join-Path $bundle "SETRON.exe") -PathType Leaf)) {
            throw "비어 있지 않은 배포 대상은 SETRON.exe가 있는 Windows 배포 폴더여야 합니다."
        }
        if ($existingBundle) {
            $external = Join-Path $bundle "SETRON_DB.sql"
            if (Test-Path -LiteralPath $external) {
                Assert-RegularPackageTree $external
                if (-not (Test-Path -LiteralPath $external -PathType Leaf) -or
                        (Get-PackageFileHash $external) -ne $requestedHash) {
                    throw "기존 외부 SETRON_DB.sql과 -DumpPath가 다릅니다. 기존 파일을 보존하기 위해 배포를 중단했습니다."
                }
                Copy-Item -LiteralPath $external -Destination (Join-Path $stagedBundle "SETRON_DB.sql") -Force
            }
            $managed = @("SETRON.exe", "SETRON_DB_INIT.exe", "prepare_demo_db.bat", "setron.env.example", "_internal", "SETRON_DB.sql")
            foreach ($entry in Get-ChildItem -LiteralPath $bundle -Force) {
                if ($entry.Name -in $managed) { continue }
                Assert-RegularPackageTree $entry.FullName
                $destination = Join-Path $stagedBundle $entry.Name
                if (Test-Path -LiteralPath $destination) {
                    throw "새 배포본과 보존할 사용자 파일의 이름이 겹칩니다: $($entry.Name)"
                }
                Copy-Item -LiteralPath $entry.FullName -Destination $destination -Recurse -Force
            }
        }
        & (Join-Path $stagedBundle "SETRON_DB_INIT.exe") --validate-dump
        if ($LASTEXITCODE -ne 0) { throw "새 시연 DB 도구의 SQL 검증 실패 (종료 코드: $LASTEXITCODE)" }
        Assert-RegularPackageTree $stagedBundle
        try {
            if ($existingBundle) {
                Move-PackageDirectory $bundle $previousBundle
                $previousMoved = $true
            }
            Move-PackageDirectory $stagedBundle $bundle
            $published = $true
        }
        catch {
            $publishError = $_
            if ($previousMoved) {
                try {
                    if (Test-Path -LiteralPath $bundle) { throw "배포 대상 경로가 사용 중입니다." }
                    Move-PackageDirectory $previousBundle $bundle
                    $previousMoved = $false
                }
                catch {
                    $keepStage = $true
                    throw "이전 배포본을 자동 복원하지 못했습니다. 복구 파일을 보존했습니다: $stage"
                }
            }
            throw $publishError
        }
        Write-Host "완료: $bundle (기존 설정·백업·사용자 파일 보존)"
    }
    finally {
        $env:Path = $originalPath
        Pop-Location
        if ($publishLock) { $publishLock.Dispose() }
        if (-not $published -and (Test-Path -LiteralPath $previousBundle)) {
            $keepStage = $true
            Write-Warning "이전 배포본을 포함한 복구 파일을 보존했습니다: $stage"
        }
        if ($stageCreated -and -not $keepStage) {
            $stageDirectory = Get-Item -LiteralPath $stage -Force -ErrorAction SilentlyContinue
            if ($stageDirectory -and $stageDirectory.FullName -eq $stage -and (Split-Path -Parent $stage) -eq $parent) {
                try {
                    Assert-RegularPackageTree $stage
                    Remove-Item -LiteralPath $stage -Recurse -Force
                }
                catch {
                    Write-Warning "임시 배포 파일을 보존했습니다: $stage"
                }
            }
        }
    }
}

# dot-source하면 빌드 없이 함수를 읽어 격리된 publish/rollback 검증에 사용할 수 있다.
if ($MyInvocation.InvocationName -ne ".") {
    Invoke-WindowsPackageBuild -RepositoryRoot (Split-Path -Parent $PSScriptRoot) @PSBoundParameters
}
