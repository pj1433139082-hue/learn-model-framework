#requires -Version 5.1
<#
.SYNOPSIS
    Build the self-contained IoT fetch probe kit.

.DESCRIPTION
    Copies the two probe scripts, the PowerShell driver, the bulk-fetch thin
    entry, the portable fetch package (iot-fetch-kit\), the operating manual and
    a verified copy of the company provider module into one folder that can be
    carried to another machine (for example the on-site computer without VPN).
    iot_bulk_fetch.py resolves iot_fetch from that copied package, so the bulk
    fetch entry keeps working inside the packed kit.

    framework/ is git-ignored in this repository, so the kit folder itself is
    added to .gitignore as well; never commit the encrypted company component.

.EXAMPLE
    .\build_kit.ps1
    .\build_kit.ps1 -OutDir D:\iot-probe-kit -IncludeLinux
    .\build_kit.ps1 -ZipPath ..\..\infra_model_deploy.zip -Force
#>
[CmdletBinding()]
param(
    [string]$OutDir,
    [string]$Framework,
    [string]$ZipPath,
    [string]$ManualPath,
    [switch]$IncludeLinux,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$here = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }

function Resolve-RepoRoot {
    $probe = $here
    for ($i = 0; $i -lt 4; $i++) {
        if (Test-Path (Join-Path $probe 'AGENTS.md')) { return (Resolve-Path $probe).Path }
        $parent = Split-Path $probe -Parent
        if (-not $parent -or $parent -eq $probe) { break }
        $probe = $parent
    }
    return $null
}

$repoRoot = Resolve-RepoRoot
if (-not $repoRoot) { throw "Could not locate the repository root above $here (AGENTS.md not found)." }

$out = if ($OutDir) { $OutDir } else { Join-Path $repoRoot '_iot-probe-kit' }
if (Test-Path -LiteralPath $out) {
    if (-not $Force) {
        throw "$out already exists. Re-run with -Force to rebuild it."
    }
    $resolved = (Resolve-Path -LiteralPath $out).Path
    if ($resolved -notlike '*_iot-probe-kit*') {
        throw "Refusing to remove $resolved : the path does not look like a probe kit folder."
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
New-Item -ItemType Directory -Path $out -Force | Out-Null
$kit = (Resolve-Path -LiteralPath $out).Path
Write-Host "kit folder: $kit" -ForegroundColor Green

# --- scripts and manual -------------------------------------------------------
$scripts = @('iot_path_probe.py', 'iot_fetch_probe.py', 'iot_bulk_fetch.py', 'iot_ledger_to_nodes.py', 'plot_signals.py', 'run_probe.ps1')
foreach ($name in $scripts) {
    $source = Join-Path $here $name
    if (-not (Test-Path -LiteralPath $source)) { throw "missing source file: $source" }
    Copy-Item -LiteralPath $source -Destination $kit -Force
    Write-Host "  copied $name"
}

# --- portable fetch package ----------------------------------------------------
# iot_bulk_fetch.py is a thin entry: it imports iot_fetch from an installed
# package, or falls back to the iot-fetch-kit\ folder next to it. Copy the whole
# package (no caches, no build leftovers) so the packed kit runs by itself.
$packageSource = Join-Path $here 'iot-fetch-kit'
if (-not (Test-Path -LiteralPath $packageSource)) {
    throw "missing source directory: $packageSource"
}
$packageTarget = Join-Path $kit 'iot-fetch-kit'
New-Item -ItemType Directory -Path $packageTarget -Force | Out-Null
$skipDirectories = @('__pycache__', '.pytest_cache')
$packageFiles = 0
foreach ($file in (Get-ChildItem -LiteralPath $packageSource -Recurse -File)) {
    $relative = $file.FullName.Substring($packageSource.Length).TrimStart('\')
    $parts = $relative -split '\\'
    $skipped = $false
    for ($i = 0; $i -lt ($parts.Length - 1); $i++) {
        if (($skipDirectories -contains $parts[$i]) -or ($parts[$i] -like '*.egg-info')) {
            $skipped = $true
            break
        }
    }
    if ($skipped -or ($file.Extension -eq '.pyc')) { continue }
    $destination = Join-Path $packageTarget $relative
    New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    $packageFiles++
}
Write-Host ("  copied iot-fetch-kit\ ({0} files)" -f $packageFiles)

if (-not $ManualPath) {
    $candidates = Get-ChildItem -LiteralPath (Join-Path $repoRoot 'docs\10-流程层') -Filter '24-*.md' -ErrorAction SilentlyContinue
    if ($candidates) { $ManualPath = $candidates[0].FullName }
}
if ($ManualPath -and (Test-Path -LiteralPath $ManualPath)) {
    Copy-Item -LiteralPath $ManualPath -Destination (Join-Path $kit 'README.md') -Force
    Write-Host ("  copied manual -> README.md ({0})" -f (Split-Path $ManualPath -Leaf))
} else {
    Write-Host "  WARNING: operating manual not found; the kit has no README.md" -ForegroundColor Yellow
}

# --- company provider module --------------------------------------------------
$frameworkSource = $Framework
$tempExtract = $null
if (-not $frameworkSource -and $ZipPath) {
    if (-not (Test-Path -LiteralPath $ZipPath)) { throw "zip not found: $ZipPath" }
    $tempExtract = Join-Path $env:TEMP ("iot-kit-extract-{0}" -f ([guid]::NewGuid().ToString('N').Substring(0, 8)))
    New-Item -ItemType Directory -Path $tempExtract -Force | Out-Null
    Write-Host "extracting $ZipPath ..." -ForegroundColor DarkGray
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $tempExtract -Force
    $module = Get-ChildItem -LiteralPath $tempExtract -Recurse -Filter 'mlops_deploy_iot_armor2_311.py' |
        Select-Object -First 1
    if (-not $module) { throw "mlops_deploy_iot_armor2_311.py not found inside $ZipPath" }
    $frameworkSource = $module.Directory.FullName
}
if (-not $frameworkSource) {
    $candidate = Join-Path $repoRoot 'framework'
    if (Test-Path (Join-Path $candidate 'mlops_deploy_iot_armor2_311.py')) { $frameworkSource = $candidate }
}

$runtimeFiles = @(
    'pyarmor_runtime_000000\__init__.py',
    'pyarmor_runtime_000000\windows_x86_64\pyarmor_runtime.pyd'
)
if ($IncludeLinux) { $runtimeFiles += 'pyarmor_runtime_000000\linux_x86_64\pyarmor_runtime.so' }

if ($frameworkSource) {
    $target = Join-Path $kit 'framework'
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    $moduleFile = Join-Path $frameworkSource 'mlops_deploy_iot_armor2_311.py'
    if (-not (Test-Path -LiteralPath $moduleFile)) {
        throw "provider module not found in $frameworkSource"
    }
    Copy-Item -LiteralPath $moduleFile -Destination $target -Force
    Write-Host "  copied mlops_deploy_iot_armor2_311.py"
    foreach ($relative in $runtimeFiles) {
        $source = Join-Path $frameworkSource $relative
        if (-not (Test-Path -LiteralPath $source)) {
            if ($IncludeLinux -and $relative -like '*linux*') { continue }
            Write-Host "  WARNING: runtime file missing: $relative" -ForegroundColor Yellow
            continue
        }
        $destination = Join-Path $target $relative
        New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination -Force
        Write-Host "  copied $relative"
    }
} else {
    Write-Host "  ERROR: no provider module available (no -Framework, no -ZipPath, no framework/)." -ForegroundColor Red
    Write-Host "         Run scripts\验收\install_company_runtime311.py first, or pass -ZipPath." -ForegroundColor Red
}
if ($tempExtract -and (Test-Path -LiteralPath $tempExtract)) {
    Remove-Item -LiteralPath $tempExtract -Recurse -Force
}

# --- digests ------------------------------------------------------------------
$sums = Join-Path $kit 'SHA256SUMS.txt'
$header = @(
    '# IoT fetch probe kit - sha256 of every file below',
    '# approved iot package   : 06f08382f652c303d451fb1770114a546babba5388247185729e46fd2e793942',
    '# approved provider .py  : 86a2a28e9474684999e2d0f661284cb6e662c69e99b7c56a1cfc4e3c468c3332',
    '# approved native.so     : 6567c0d8be7192482f5998b923dd7c10b81c66f02ca767761ae8ce6778519f79',
    '# note: pyarmor_runtime_000000/__init__.py is stored here with LF line endings;',
    '#       the approved package stores it with CRLF (b84c5aee91521a6c83fdadd137c3c806',
    '#       b5064aa814c11c8e84f593c4d215d9d0). Same content, different line endings.',
    '# verify with:  Get-FileHash <file> -Algorithm SHA256'
)
$lines = New-Object System.Collections.Generic.List[string]
$lines.AddRange([string[]]$header)
Get-ChildItem -LiteralPath $kit -Recurse -File | Where-Object { $_.Name -ne 'SHA256SUMS.txt' } | Sort-Object FullName | ForEach-Object {
    $relative = $_.FullName.Substring($kit.Length).TrimStart('\')
    $digest = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower()
    $lines.Add(('{0}  {1}' -f $digest, $relative))
}
Set-Content -LiteralPath $sums -Value $lines -Encoding UTF8
Write-Host "  wrote SHA256SUMS.txt"

# --- report -------------------------------------------------------------------
Write-Host ""
Write-Host "--- KIT CONTENTS ---" -ForegroundColor Cyan
Get-ChildItem -LiteralPath $kit -Recurse -File | Sort-Object FullName | ForEach-Object {
    Write-Host ("  {0,10}  {1}" -f $_.Length, $_.FullName.Substring($kit.Length).TrimStart('\'))
}
Write-Host ""
Write-Host "--- NEXT STEPS ---" -ForegroundColor Cyan
Write-Host "1. Copy the whole folder to the target machine (USB or share):"
Write-Host "     $kit"
Write-Host "2. There, in PowerShell, from the copied folder:"
Write-Host "     .\run_probe.ps1 -Zip <infra_model_deploy.zip>"
Write-Host "     .\run_probe.ps1 -FactoryCode <CODE> -Point <POINT_CODE> -Zip <infra_model_deploy.zip>"
Write-Host "3. Bring back the files in logs\ (02-fetch-*.log / 02-fetch-*.json)."
Write-Host ""
Write-Host "Python 3.11 is required for step 2; any Python 3.8+ runs step 1." -ForegroundColor DarkGray
