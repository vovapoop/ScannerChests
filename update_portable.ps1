param(
    [string]$Source = (Join-Path $PSScriptRoot "ChestScanner"),
    [string]$Target
)

$ErrorActionPreference = "Stop"

function Invoke-Robocopy {
    param([string[]]$Arguments)
    & robocopy @Arguments
    $code = $LASTEXITCODE
    if ($code -ge 8) {
        throw "Robocopy failed with exit code $code."
    }
}

function Restore-Directory {
    param([string]$BackupPath, [string]$TargetPath, [bool]$Existed, [switch]$PreserveCustom)
    if ($Existed) {
        $null = New-Item -ItemType Directory -Path $TargetPath -Force
        $copyArgs = @($BackupPath, $TargetPath, "/MIR", "/COPY:DAT", "/DCOPY:DAT", "/R:2", "/W:1")
        if ($PreserveCustom) { $copyArgs += @("/XD", "custom") }
        Invoke-Robocopy -Arguments $copyArgs
    }
    elseif (Test-Path -LiteralPath $TargetPath) {
        Remove-Item -LiteralPath $TargetPath -Recurse -Force
    }
}

function Restore-File {
    param([string]$BackupPath, [string]$TargetPath, [bool]$Existed)
    if ($Existed) {
        Copy-Item -LiteralPath $BackupPath -Destination $TargetPath -Force
    }
    elseif (Test-Path -LiteralPath $TargetPath) {
        Remove-Item -LiteralPath $TargetPath -Force
    }
}

if (-not $Target) {
    $Target = Read-Host "Enter the full path to the existing ChestScanner folder"
}
if (-not $Target) {
    Write-Host "[ERROR] Target folder was not specified." -ForegroundColor Red
    exit 1
}

$Source = [IO.Path]::GetFullPath($Source)
$Target = [IO.Path]::GetFullPath($Target.Trim('"'))
$required = @(
    @{ Path = (Join-Path $Source "ChestScanner.exe"); Type = "Leaf" },
    @{ Path = (Join-Path $Source "_internal"); Type = "Container" },
    @{ Path = (Join-Path $Source "assets"); Type = "Container" },
    @{ Path = (Join-Path $Source "LICENSE"); Type = "Leaf" }
)
foreach ($entry in $required) {
    if (-not (Test-Path -LiteralPath $entry.Path -PathType $entry.Type)) {
        Write-Host "[ERROR] Required package file or folder is missing: $($entry.Path)" -ForegroundColor Red
        exit 1
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $Target "ChestScanner.exe") -PathType Leaf)) {
    Write-Host "[ERROR] ChestScanner.exe was not found in the target folder." -ForegroundColor Red
    exit 1
}
foreach ($directoryName in @("_internal", "assets")) {
    $directoryPath = Join-Path $Target $directoryName
    if ((Test-Path -LiteralPath $directoryPath) -and
        -not (Test-Path -LiteralPath $directoryPath -PathType Container)) {
        Write-Host "[ERROR] Target path is not a directory: $directoryPath" -ForegroundColor Red
        exit 1
    }
}
if (Get-Process -Name "ChestScanner" -ErrorAction SilentlyContinue) {
    Write-Host "[ERROR] Close Chest Scanner before updating." -ForegroundColor Red
    exit 1
}

$backup = Join-Path $env:TEMP ("ChestScanner-update-backup-" + [guid]::NewGuid().ToString("N"))
$backupInternal = Join-Path $backup "_internal"
$backupAssets = Join-Path $backup "assets"
$backupExe = Join-Path $backup "ChestScanner.exe"
$backupLicense = Join-Path $backup "LICENSE"
$targetInternal = Join-Path $Target "_internal"
$targetAssets = Join-Path $Target "assets"
$targetExe = Join-Path $Target "ChestScanner.exe"
$targetLicense = Join-Path $Target "LICENSE"
$hadInternal = Test-Path -LiteralPath $targetInternal -PathType Container
$hadAssets = Test-Path -LiteralPath $targetAssets -PathType Container
$hadExe = Test-Path -LiteralPath $targetExe -PathType Leaf
$hadLicense = Test-Path -LiteralPath $targetLicense -PathType Leaf
$updateStarted = $false
$rollbackSucceeded = $true

try {
    $null = New-Item -ItemType Directory -Path $backup -Force
    if ($hadInternal) {
        Invoke-Robocopy -Arguments @($targetInternal, $backupInternal, "/MIR", "/COPY:DAT", "/DCOPY:DAT", "/R:2", "/W:1")
    }
    if ($hadAssets) {
        Invoke-Robocopy -Arguments @($targetAssets, $backupAssets, "/MIR", "/XD", "custom", "/COPY:DAT", "/DCOPY:DAT", "/R:2", "/W:1")
    }
    if ($hadExe) { Copy-Item -LiteralPath $targetExe -Destination $backupExe }
    if ($hadLicense) { Copy-Item -LiteralPath $targetLicense -Destination $backupLicense }

    $updateStarted = $true
    Write-Host "Backup of program files created. Updating..." -ForegroundColor Cyan
    Invoke-Robocopy -Arguments @((Join-Path $Source "_internal"), $targetInternal, "/MIR", "/COPY:DAT", "/DCOPY:DAT", "/R:2", "/W:1")
    Invoke-Robocopy -Arguments @((Join-Path $Source "assets"), $targetAssets, "/MIR", "/XD", "custom", "/COPY:DAT", "/DCOPY:DAT", "/R:2", "/W:1")
    Copy-Item -LiteralPath (Join-Path $Source "LICENSE") -Destination $targetLicense -Force
    Copy-Item -LiteralPath (Join-Path $Source "ChestScanner.exe") -Destination $targetExe -Force

    Write-Host "[OK] Portable version updated." -ForegroundColor Green
    Write-Host "User database, settings, profiles, history and custom images were preserved."
}
catch {
    Write-Host "[ERROR] Update failed: $($_.Exception.Message)" -ForegroundColor Red
    if ($updateStarted) {
        Write-Host "Restoring previous program files..." -ForegroundColor Yellow
        try {
            Restore-Directory -BackupPath $backupInternal -TargetPath $targetInternal -Existed $hadInternal
            Restore-Directory -BackupPath $backupAssets -TargetPath $targetAssets -Existed $hadAssets -PreserveCustom
            Restore-File -BackupPath $backupExe -TargetPath $targetExe -Existed $hadExe
            Restore-File -BackupPath $backupLicense -TargetPath $targetLicense -Existed $hadLicense
            Write-Host "[OK] Previous version restored." -ForegroundColor Green
        }
        catch {
            $rollbackSucceeded = $false
            Write-Host "[CRITICAL] Rollback failed: $($_.Exception.Message)" -ForegroundColor Red
            Write-Host "Backup retained at: $backup" -ForegroundColor Yellow
        }
    }
    if ($rollbackSucceeded) {
        Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue
    }
    exit 1
}

Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction SilentlyContinue
exit 0
