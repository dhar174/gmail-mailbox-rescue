<#
.SYNOPSIS
    Builds the standalone Windows distribution of Mailbox Rescue using PyInstaller.

.DESCRIPTION
    Compiles Mailbox Rescue into a windowed GUI executable and one-folder distribution,
    verifies release hygiene (preventing accidental bundling of credentials or mail archives),
    and packages a versioned release ZIP suitable for coworker distribution.

.PARAMETER Clean
    Removes existing build/, dist/Mailbox Rescue/, and dist/releases/ directories before building.

.PARAMETER OAuthClientConfig
    Optional path to an explicit Google OAuth client_secret.json to stage beside the executable
    for a pilot/test distribution. Must be an intentional file path. User tokens are strictly rejected.

.PARAMETER SkipZip
    Skips creating the final release ZIP package.

.EXAMPLE
    .\scripts\build-windows.ps1 -Clean

.EXAMPLE
    .\scripts\build-windows.ps1 -Clean -OAuthClientConfig "C:\secure\client_secret.json"
#>

[CmdletBinding()]
param(
    [switch]$Clean,
    [string]$OAuthClientConfig = "",
    [switch]$SkipZip
)

$ErrorActionPreference = "Stop"

# 1. Locate repository root
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Mailbox Rescue - Windows Packaging" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Repository root: $RepoRoot"

# 2. Check Python environment
$PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) {
    Write-Error "Python executable not found in PATH."
    exit 1
}

$PyVersion = python --version 2>&1
Write-Host "Python: $PyVersion"

# Check PyInstaller availability
$PyInstallerCheck = python -c "import PyInstaller; print(PyInstaller.__version__)" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller is not installed. Run: python -m pip install -e `".[dev]`""
    exit 1
}
Write-Host "PyInstaller: v$PyInstallerCheck"

# Package version
$AppVersion = "0.1.0"
Write-Host "Application Version: $AppVersion"

# 3. Clean if requested
$DistDir = Join-Path $RepoRoot "dist"
$BuildDir = Join-Path $RepoRoot "build"
$ReleaseDir = Join-Path $DistDir "releases"
$AppOutDir = Join-Path $DistDir "Mailbox Rescue"

if ($Clean) {
    Write-Host "`nCleaning previous build artifacts..." -ForegroundColor Yellow
    if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
    if (Test-Path $AppOutDir) { Remove-Item -Recurse -Force $AppOutDir }
    if (Test-Path $ReleaseDir) { Remove-Item -Recurse -Force $ReleaseDir }
}

# 4. Run PyInstaller
Write-Host "`nBuilding standalone executable with PyInstaller..." -ForegroundColor Green
$SpecFile = Join-Path $RepoRoot "packaging\mailbox-rescue.spec"

& python -m PyInstaller --clean $SpecFile --distpath $DistDir --workpath $BuildDir
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller build failed with exit code $LASTEXITCODE."
    exit 1
}

$ExePath = Join-Path $AppOutDir "Mailbox Rescue.exe"
if (-not (Test-Path $ExePath)) {
    Write-Error "Build output executable not found at: $ExePath"
    exit 1
}
Write-Host "Executable built successfully: $ExePath" -ForegroundColor Green

# 5. Copy Coworker START HERE instructions
$StartHereSrc = Join-Path $RepoRoot "START HERE.txt"
if (Test-Path $StartHereSrc) {
    Copy-Item $StartHereSrc -Destination $AppOutDir -Force
    Write-Host "Included START HERE.txt in application bundle."
}

# 6. Handle optional sidecar OAuth client configuration
$AllowOAuthFlag = @()
if ($OAuthClientConfig -and $OAuthClientConfig.Trim() -ne "") {
    $ResolvedOAuth = Resolve-Path $OAuthClientConfig -ErrorAction SilentlyContinue
    if (-not $ResolvedOAuth -or -not (Test-Path $ResolvedOAuth.Path -PathType Leaf)) {
        Write-Error "Specified OAuth client config does not exist: $OAuthClientConfig"
        exit 1
    }

    # Strict safety check: reject tokens or database files passed as client config
    $OAuthFileName = Split-Path -Leaf $ResolvedOAuth.Path
    if ($OAuthFileName -match "token" -or $OAuthFileName -match "\.sqlite" -or $OAuthFileName -match "\.eml" -or $OAuthFileName -match "\.mbox") {
        Write-Error "Security check rejected file '$OAuthFileName': User tokens and mail data cannot be packaged!"
        exit 1
    }

    $DestSecretPath = Join-Path $AppOutDir "client_secret.json"
    Copy-Item $ResolvedOAuth.Path -Destination $DestSecretPath -Force
    Write-Host "`n[NOTICE] Staged OAuth client config beside executable: $DestSecretPath" -ForegroundColor Magenta
    Write-Host "[NOTICE] Never distribute employee tokens or commit this file to git." -ForegroundColor Magenta
    $AllowOAuthFlag = @("--allow-oauth-client")
}

# 7. Verify release hygiene on application directory
Write-Host "`nVerifying release hygiene on staged bundle..." -ForegroundColor Cyan
$HygieneScript = Join-Path $RepoRoot "scripts\verify_release_hygiene.py"
& python $HygieneScript $AppOutDir @AllowOAuthFlag
if ($LASTEXITCODE -ne 0) {
    Write-Error "Release hygiene check failed for directory: $AppOutDir"
    exit 1
}

# 8. Create versioned release ZIP unless skipped
if (-not $SkipZip) {
    if (-not (Test-Path $ReleaseDir)) {
        New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null
    }

    $ZipFileName = "Mailbox-Rescue-v$AppVersion-win64.zip"
    $ZipPath = Join-Path $ReleaseDir $ZipFileName

    if (Test-Path $ZipPath) {
        Remove-Item -Force $ZipPath
    }

    Write-Host "`nCreating release ZIP: $ZipPath..." -ForegroundColor Green
    # Compress the folder so the root of the ZIP contains 'Mailbox Rescue/...'
    Compress-Archive -Path $AppOutDir -DestinationPath $ZipPath -CompressionLevel Optimal

    if (-not (Test-Path $ZipPath)) {
        Write-Error "Failed to produce release ZIP at: $ZipPath"
        exit 1
    }

    $ZipItem = Get-Item $ZipPath
    $ZipSizeMB = [math]::Round($ZipItem.Length / 1MB, 2)
    Write-Host "Release ZIP created successfully ($ZipSizeMB MB): $ZipPath" -ForegroundColor Green

    # Verify release hygiene on the final ZIP
    Write-Host "`nVerifying release hygiene on release ZIP..." -ForegroundColor Cyan
    & python $HygieneScript $ZipPath @AllowOAuthFlag
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Release hygiene check failed for ZIP: $ZipPath"
        exit 1
    }
}

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  Build and Packaging Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "App Directory : $AppOutDir"
Write-Host "Executable    : $ExePath"
if (-not $SkipZip) {
    Write-Host "Release ZIP   : $ZipPath ($ZipSizeMB MB)"
}
Write-Host "========================================`n"
