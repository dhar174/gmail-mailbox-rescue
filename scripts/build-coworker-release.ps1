<#
.SYNOPSIS
    Builds a private, coworker-ready Mailbox Rescue Windows release.

.DESCRIPTION
    This script:
      - verifies the repository is clean
      - verifies the OAuth Desktop client config exists
      - validates the OAuth client without printing its contents
      - optionally runs Ruff and pytest
      - calls the existing official Windows packaging script
      - stages exactly one client_secret.json inside the packaged app
      - verifies release hygiene
      - verifies START HERE.txt and client_secret.json are in the ZIP
      - computes a SHA-256 checksum
      - creates a small release manifest
      - creates dist\coworker-release containing only:
          * the coworker-ready ZIP
          * its SHA-256 manifest

    It NEVER places a loose client_secret.json in the coworker-release folder.

.PARAMETER OAuthClientConfig
    Path to the Google Desktop OAuth client_secret.json.

.PARAMETER SkipTests
    Skip Ruff and pytest before packaging.

.EXAMPLE
    .\scripts\build-coworker-release.ps1 `
        -OAuthClientConfig "C:\secure\mailbox-rescue\client_secret.json"

.EXAMPLE
    .\scripts\build-coworker-release.ps1 `
        -OAuthClientConfig "C:\secure\mailbox-rescue\client_secret.json" `
        -SkipTests
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OAuthClientConfig,

    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

function Fail {
    param([string]$Message)
    Write-Host ""
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

function Section {
    param([string]$Message)
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host " $Message" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
}

# ------------------------------------------------------------
# Locate repository root
# ------------------------------------------------------------

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

Section "Mailbox Rescue - Coworker Release Builder"

Write-Host "Repository : $RepoRoot"
Write-Host "OAuth file : $OAuthClientConfig"

# ------------------------------------------------------------
# Required repository files
# ------------------------------------------------------------

$BuildScript = Join-Path $RepoRoot "scripts\build-windows.ps1"
$HygieneScript = Join-Path $RepoRoot "scripts\verify_release_hygiene.py"
$StartHere = Join-Path $RepoRoot "START HERE.txt"

if (-not (Test-Path $BuildScript -PathType Leaf)) {
    Fail "Missing build script: $BuildScript"
}

if (-not (Test-Path $HygieneScript -PathType Leaf)) {
    Fail "Missing hygiene script: $HygieneScript"
}

if (-not (Test-Path $StartHere -PathType Leaf)) {
    Fail "START HERE.txt is missing. Refusing to create a coworker release."
}

# ------------------------------------------------------------
# Verify OAuth config exists
# ------------------------------------------------------------

Section "Checking OAuth client configuration"

$ResolvedOAuth = Resolve-Path $OAuthClientConfig -ErrorAction SilentlyContinue

if (-not $ResolvedOAuth) {
    Fail "OAuth client configuration not found: $OAuthClientConfig"
}

$OAuthPath = $ResolvedOAuth.Path

if (-not (Test-Path $OAuthPath -PathType Leaf)) {
    Fail "OAuth configuration is not a regular file: $OAuthPath"
}

# Intentionally do NOT read or print the JSON contents.
& python $HygieneScript --validate-oauth-client $OAuthPath

if ($LASTEXITCODE -ne 0) {
    Fail "OAuth Desktop client validation failed."
}

Write-Host "OAuth Desktop client validation: PASS" -ForegroundColor Green

# ------------------------------------------------------------
# Verify clean Git working tree
# ------------------------------------------------------------

Section "Checking repository state"

$GitStatus = git status --porcelain

if ($LASTEXITCODE -ne 0) {
    Fail "Unable to read Git repository status."
}

if ($GitStatus) {
    Write-Host $GitStatus
    Fail "Working tree is not clean. Commit, stash, or discard changes before building."
}

$SourceSha = (git rev-parse HEAD).Trim()

if ($LASTEXITCODE -ne 0 -or -not $SourceSha) {
    Fail "Unable to determine source Git SHA."
}

$Branch = (git branch --show-current).Trim()

Write-Host "Branch     : $Branch"
Write-Host "Source SHA : $SourceSha"
Write-Host "Working tree: CLEAN" -ForegroundColor Green

# ------------------------------------------------------------
# Environment checks
# ------------------------------------------------------------

Section "Checking build environment"

$PythonVersion = (python --version 2>&1).ToString().Trim()

if ($LASTEXITCODE -ne 0) {
    Fail "Python is not available."
}

$PythonBits = (python -c "import struct; print(struct.calcsize('P') * 8)").Trim()

if ($PythonBits -ne "64") {
    Fail "A win64 release requires 64-bit Python. Detected $PythonBits-bit."
}

$PyInstallerVersion = (
    python -c "import PyInstaller; print(PyInstaller.__version__)" 2>&1
).ToString().Trim()

if ($LASTEXITCODE -ne 0) {
    Fail 'PyInstaller is unavailable. Run: python -m pip install -e ".[dev]"'
}

Write-Host "Python      : $PythonVersion"
Write-Host "Architecture: $PythonBits-bit"
Write-Host "PyInstaller : $PyInstallerVersion"

# ------------------------------------------------------------
# Tests
# ------------------------------------------------------------

if (-not $SkipTests) {
    Section "Running pre-release checks"

    Write-Host "Running Ruff..."
    & python -m ruff check .

    if ($LASTEXITCODE -ne 0) {
        Fail "Ruff failed. Release aborted."
    }

    Write-Host ""
    Write-Host "Running pytest..."
    & python -m pytest

    if ($LASTEXITCODE -ne 0) {
        Fail "pytest failed. Release aborted."
    }

    Write-Host ""
    Write-Host "Pre-release checks: PASS" -ForegroundColor Green
}
else {
    Write-Host ""
    Write-Host "WARNING: Ruff and pytest skipped by request." -ForegroundColor Yellow
}

# ------------------------------------------------------------
# Build configured Windows release
# ------------------------------------------------------------

Section "Building configured coworker release"

& $BuildScript `
    -Clean `
    -OAuthClientConfig $OAuthPath

if ($LASTEXITCODE -ne 0) {
    Fail "Windows release build failed."
}

# ------------------------------------------------------------
# Locate generated ZIP
# ------------------------------------------------------------

$ReleaseDir = Join-Path $RepoRoot "dist\releases"

if (-not (Test-Path $ReleaseDir -PathType Container)) {
    Fail "Release directory was not created: $ReleaseDir"
}

$ReleaseZips = @(
    Get-ChildItem `
        -Path $ReleaseDir `
        -Filter "Mailbox-Rescue-v*-win64.zip" `
        -File
)

if ($ReleaseZips.Count -ne 1) {
    Fail "Expected exactly one Mailbox Rescue release ZIP, found $($ReleaseZips.Count)."
}

$ReleaseZip = $ReleaseZips[0]
$ReleaseZipPath = $ReleaseZip.FullName

Write-Host "Generated ZIP: $ReleaseZipPath"

# ------------------------------------------------------------
# Re-run hygiene check explicitly
# ------------------------------------------------------------

Section "Verifying final release hygiene"

& python $HygieneScript `
    $ReleaseZipPath `
    --allow-oauth-client

if ($LASTEXITCODE -ne 0) {
    Fail "Final release ZIP failed hygiene verification."
}

Write-Host "Release hygiene: PASS" -ForegroundColor Green

# ------------------------------------------------------------
# Inspect ZIP structure without reading secret contents
# ------------------------------------------------------------

Section "Checking coworker ZIP contents"

Add-Type -AssemblyName System.IO.Compression.FileSystem

$Zip = [System.IO.Compression.ZipFile]::OpenRead($ReleaseZipPath)

try {
    $Entries = @($Zip.Entries | ForEach-Object { $_.FullName.Replace("\", "/") })

    $ExpectedExe = "Mailbox Rescue/Mailbox Rescue.exe"
    $ExpectedOAuth = "Mailbox Rescue/client_secret.json"
    $ExpectedStartHere = "Mailbox Rescue/START HERE.txt"

    foreach ($Required in @(
        $ExpectedExe,
        $ExpectedOAuth,
        $ExpectedStartHere
    )) {
        if ($Entries -notcontains $Required) {
            Fail "Required ZIP entry missing: $Required"
        }
    }

    $OAuthEntries = @(
        $Entries | Where-Object {
            $Leaf = Split-Path $_ -Leaf
            $Leaf -like "client_secret*.json" -or
            $Leaf -like "client_secrets*.json"
        }
    )

    if ($OAuthEntries.Count -ne 1) {
        Fail "Expected exactly one OAuth client configuration in ZIP; found $($OAuthEntries.Count)."
    }

    if ($OAuthEntries[0] -ne $ExpectedOAuth) {
        Fail "OAuth client configuration is in the wrong ZIP location: $($OAuthEntries[0])"
    }

    # Defense-in-depth token filename check.
    $ForbiddenTokenEntries = @(
        $Entries | Where-Object {
            $Leaf = (Split-Path $_ -Leaf).ToLowerInvariant()

            $Leaf -eq "google_token.json" -or
            $Leaf -eq "token.json" -or
            $Leaf -eq "credentials.json"
        }
    )

    if ($ForbiddenTokenEntries.Count -gt 0) {
        Fail "User token/credential file detected in ZIP."
    }

    Write-Host "Mailbox Rescue.exe     : PRESENT" -ForegroundColor Green
    Write-Host "START HERE.txt         : PRESENT" -ForegroundColor Green
    Write-Host "client_secret.json     : PRESENT, exactly once" -ForegroundColor Green
    Write-Host "User OAuth tokens      : NONE" -ForegroundColor Green
}
finally {
    if ($Zip) {
        $Zip.Dispose()
    }
}

# ------------------------------------------------------------
# SHA-256 checksum
# ------------------------------------------------------------

Section "Generating SHA-256 manifest"

$Hash = Get-FileHash `
    -Path $ReleaseZipPath `
    -Algorithm SHA256

$ZipSizeMB = [math]::Round($ReleaseZip.Length / 1MB, 2)

$BaseName = [System.IO.Path]::GetFileNameWithoutExtension($ReleaseZip.Name)
$ManifestName = "$BaseName-SHA256.txt"

$ManifestPath = Join-Path $ReleaseDir $ManifestName

$Manifest = @"
Mailbox Rescue - Coworker Release

Source commit:
$SourceSha

Artifact:
$($ReleaseZip.Name)

Size:
$ZipSizeMB MB

SHA-256:
$($Hash.Hash)

This package contains the intentionally staged Google Desktop OAuth
client configuration required for Mailbox Rescue sign-in.

It must NOT contain any user's OAuth token or exported mailbox data.
"@

Set-Content `
    -Path $ManifestPath `
    -Value $Manifest `
    -Encoding UTF8

Write-Host "SHA-256: $($Hash.Hash)"

# ------------------------------------------------------------
# Create clean coworker distribution directory
# ------------------------------------------------------------

Section "Creating final coworker distribution folder"

$CoworkerDir = Join-Path $RepoRoot "dist\coworker-release"

if (Test-Path $CoworkerDir) {
    Remove-Item `
        -Path $CoworkerDir `
        -Recurse `
        -Force
}

New-Item `
    -ItemType Directory `
    -Path $CoworkerDir `
    -Force | Out-Null

$FinalZip = Join-Path $CoworkerDir $ReleaseZip.Name
$FinalManifest = Join-Path $CoworkerDir $ManifestName

Copy-Item `
    -Path $ReleaseZipPath `
    -Destination $FinalZip

Copy-Item `
    -Path $ManifestPath `
    -Destination $FinalManifest

# ------------------------------------------------------------
# Verify coworker directory contains exactly two files
# ------------------------------------------------------------

$CoworkerFiles = @(
    Get-ChildItem `
        -Path $CoworkerDir `
        -File
)

if ($CoworkerFiles.Count -ne 2) {
    Fail "Coworker release directory should contain exactly two files."
}

$LooseSecrets = @(
    Get-ChildItem `
        -Path $CoworkerDir `
        -Filter "*.json" `
        -File `
        -ErrorAction SilentlyContinue
)

if ($LooseSecrets.Count -gt 0) {
    Fail "Unexpected loose JSON credential/config file found in coworker release directory."
}

# Verify copied ZIP hash matches original.
$FinalHash = Get-FileHash `
    -Path $FinalZip `
    -Algorithm SHA256

if ($FinalHash.Hash -ne $Hash.Hash) {
    Fail "Copied coworker ZIP checksum does not match source release ZIP."
}

# ------------------------------------------------------------
# Final report
# ------------------------------------------------------------

Section "COWORKER RELEASE READY"

Write-Host "Source SHA :" $SourceSha
Write-Host "ZIP        :" $FinalZip
Write-Host "Size       :" "$ZipSizeMB MB"
Write-Host "SHA-256    :" $Hash.Hash
Write-Host "Manifest   :" $FinalManifest

Write-Host ""
Write-Host "Final folder contents:" -ForegroundColor Cyan

Get-ChildItem $CoworkerDir |
    Select-Object Name, Length |
    Format-Table -AutoSize

Write-Host ""
Write-Host "The ZIP contains:" -ForegroundColor Green
Write-Host "  Mailbox Rescue.exe"
Write-Host "  START HERE.txt"
Write-Host "  client_secret.json"
Write-Host "  required PyInstaller runtime files"

Write-Host ""
Write-Host "It does NOT contain:" -ForegroundColor Green
Write-Host "  google_token.json"
Write-Host "  exported email"
Write-Host "  checkpoint databases"
Write-Host "  loose user credentials"

Write-Host ""
Write-Host "Coworker-ready Mailbox Rescue package created successfully." `
    -ForegroundColor Green