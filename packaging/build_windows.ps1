# Windows 배포본: GUI와 빈 DB 스키마 설치 EXE/BAT를 함께 만든다.
# powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()]
    [string]$OutputDir = "",
    [string]$ReuseGuiFrom = ""
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

function Copy-PackageUserEntry($source, $destination, $relative, $excluded) {
    if ($relative -in $excluded) { return }
    $entry = Get-Item -LiteralPath $source -Force
    if (Test-Path -LiteralPath $destination) {
        throw "새 배포본과 보존할 사용자 파일의 이름이 겹칩니다: $relative"
    }
    if ($entry.PSIsContainer) {
        [IO.Directory]::CreateDirectory($destination) | Out-Null
        foreach ($child in Get-ChildItem -LiteralPath $source -Force) {
            Copy-PackageUserEntry $child.FullName (Join-Path $destination $child.Name) (Join-Path $relative $child.Name) $excluded
        }
    } else {
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
}

function Invoke-WindowsPackageBuild {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [string]$OutputDir = "",
        [string]$ReuseGuiFrom = ""
    )
    $root = (Resolve-Path -LiteralPath $RepositoryRoot).ProviderPath
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
    $reuse = $null
    if ($ReuseGuiFrom) {
        $reuse = (Resolve-Path -LiteralPath $ReuseGuiFrom).ProviderPath
        foreach ($name in @("SETRON.exe", "_internal")) {
            $entry = Join-Path $reuse $name
            if (-not (Test-Path -LiteralPath $entry)) { throw "재사용할 GUI 파일이 없습니다: $entry" }
            Assert-RegularPackageTree $entry
        }
        if (-not (Test-Path -LiteralPath (Join-Path $reuse "SETRON.exe") -PathType Leaf) -or
                -not (Test-Path -LiteralPath (Join-Path $reuse "_internal") -PathType Container)) {
            throw "재사용 대상에는 SETRON.exe와 _internal 폴더가 있어야 합니다."
        }
        $demoRuntime = Get-ChildItem -LiteralPath (Join-Path $reuse "_internal") -Recurse -File -Force |
            Where-Object { $_.Name -in @("SETRON_DB.sql", "dataset.dump", "SETRON_DB_INIT.exe", "prepare_demo_db.bat") }
        if ($demoRuntime) { throw "시연 자료가 포함된 런타임은 재사용할 수 없습니다. 전체 빌드를 실행해 주세요." }
    }
    $uv = @(Get-Command uv -CommandType Application)[0].Source
    $stage = [IO.Path]::GetFullPath((Join-Path $parent ("setron-stage-" + [guid]::NewGuid().ToString("N"))))
    $stagedBundle = Join-Path $stage "SETRON"
    $previousBundle = Join-Path $stage "previous-SETRON"
    $lockPath = Join-Path $parent ".setron-publish.lock"
    $executables = @((Join-Path $bundle "SETRON.exe"), (Join-Path $bundle "SETRON_DB_SETUP.exe"), (Join-Path $bundle "SETRON_DB_INIT.exe"))
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
        & $uv sync --locked
        if ($LASTEXITCODE -ne 0) { throw "uv sync 실패 (종료 코드: $LASTEXITCODE)" }
        $env:Path = "$env:SystemRoot\System32;$env:SystemRoot"
        if ($reuse) {
            [IO.Directory]::CreateDirectory($stagedBundle) | Out-Null
            Copy-Item -LiteralPath (Join-Path $reuse "SETRON.exe") -Destination $stagedBundle
            Copy-Item -LiteralPath (Join-Path $reuse "_internal") -Destination $stagedBundle -Recurse
        } else {
            & $uv run --locked --no-sync pyinstaller packaging\setron.spec --noconfirm --distpath $stage --workpath (Join-Path $stage "work")
            if ($LASTEXITCODE -ne 0) { throw "GUI 빌드 실패 (종료 코드: $LASTEXITCODE)" }
        }
        & $uv run --locked --no-sync python packaging\build_schema_db.py --output-dir $stagedBundle
        if ($LASTEXITCODE -ne 0) { throw "DB 스키마 설치 도구 빌드 실패 (종료 코드: $LASTEXITCODE)" }
        Copy-Item -LiteralPath (Join-Path $root "setron.env.example") -Destination $stagedBundle
        foreach ($name in @("SETRON.exe", "SETRON_DB_SETUP.exe", "prepare_schema.bat", "setron.env.example", "_internal\database\schema_mysql5619.sql")) {
            if (-not (Test-Path -LiteralPath (Join-Path $stagedBundle $name) -PathType Leaf)) {
                throw "새 배포본에 필요한 파일이 없습니다: $name"
            }
        }

        Assert-RegularPackagePath $lockPath
        $publishLock = [IO.FileStream]::new($lockPath,
            [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None, 4096,
            [IO.FileOptions]::DeleteOnClose)
        $running = Get-Process -Name SETRON, SETRON_DB_SETUP, SETRON_DB_INIT -ErrorAction SilentlyContinue | Where-Object {
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
        $legacy = @("SETRON_DB_INIT.exe", "prepare_demo_db.bat", "SETRON_DB.sql", "database\SETRON_DB.sql")
        $archive = Join-Path $parent "SETRON-demo-archive"
        $retired = @()
        if ($existingBundle) {
            foreach ($relative in $legacy) {
                $source = Join-Path $bundle $relative
                if (-not (Test-Path -LiteralPath $source)) { continue }
                Assert-RegularPackageTree $source
                if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "기존 시연 자료는 일반 파일이어야 합니다: $source" }
                $destination = Join-Path $archive $relative
                Assert-RegularPackagePath $destination
                if ((Test-Path -LiteralPath $destination) -and
                        (-not (Test-Path -LiteralPath $destination -PathType Leaf) -or
                         (Get-PackageFileHash $source) -ne (Get-PackageFileHash $destination))) {
                    throw "보관 폴더에 다른 파일이 있어 덮어쓰지 않았습니다: $destination"
                }
                $retired += @{ Source = $source; Destination = $destination }
            }
            $managed = @("SETRON.exe", "SETRON_DB_SETUP.exe", "prepare_schema.bat", "setron.env.example", "_internal") + $legacy
            foreach ($entry in Get-ChildItem -LiteralPath $bundle -Force) {
                if ($entry.Name -in $managed) { continue }
                Assert-RegularPackageTree $entry.FullName
                $destination = Join-Path $stagedBundle $entry.Name
                Copy-PackageUserEntry $entry.FullName $destination $entry.Name $legacy
            }
        }
        & (Join-Path $stagedBundle "SETRON_DB_SETUP.exe") --validate-schema
        if ($LASTEXITCODE -ne 0) { throw "새 DB 설치 도구의 스키마 검증 실패 (종료 코드: $LASTEXITCODE)" }
        Assert-RegularPackageTree $stagedBundle
        foreach ($file in $retired) {
            if (-not (Test-Path -LiteralPath $file.Destination)) {
                [IO.Directory]::CreateDirectory((Split-Path -Parent $file.Destination)) | Out-Null
                Assert-RegularPackagePath $file.Destination
                Copy-Item -LiteralPath $file.Source -Destination $file.Destination
            }
        }
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
