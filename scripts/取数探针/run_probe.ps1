#requires -Version 5.1
<#
.SYNOPSIS
    Run the IoT network-path probe, and optionally the real fetch probe.

.DESCRIPTION
    Step 1 runs iot_path_probe.py on any Python 3.8+ and answers "does this
    machine reach the IoT endpoints over the public route or the intranet one".
    Step 2 runs iot_fetch_probe.py on Python 3.11 with the company provider and
    records the DNS / TCP / TLS / HTTP trail of a real fetch_data call.

    Both runs are teed into a log file so the output can be copied back.

.EXAMPLE
    .\run_probe.ps1
    .\run_probe.ps1 -FactoryCode <CODE> -Point <POINT_CODE>
    .\run_probe.ps1 -Framework ..\framework -FactoryCode <CODE> -Point <POINT_CODE> -Zip <infra_model_deploy.zip>
#>
[CmdletBinding()]
param(
    [string]$RepoRoot,
    [string]$Framework,
    [string]$FactoryCode,
    [string[]]$Point,
    [string]$Start,
    [string]$RollingWindow = '60min',
    [string]$Interval = '15s',
    [string]$Zip,
    [string]$OutRoot,
    [string[]]$Map,
    [switch]$SkipPathProbe,
    [switch]$RespectProxy,
    [switch]$BypassProxy
)

$ErrorActionPreference = 'Stop'
$here = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }

function Resolve-RepoRoot {
    param([string]$Hint)
    if ($Hint -and (Test-Path (Join-Path $Hint 'AGENTS.md'))) { return (Resolve-Path $Hint).Path }
    if ($env:MODEL_LAB_REPO -and (Test-Path (Join-Path $env:MODEL_LAB_REPO 'AGENTS.md'))) {
        return (Resolve-Path $env:MODEL_LAB_REPO).Path
    }
    $probe = $here
    for ($i = 0; $i -lt 4; $i++) {
        if (Test-Path (Join-Path $probe 'AGENTS.md')) { return (Resolve-Path $probe).Path }
        $parent = Split-Path $probe -Parent
        if (-not $parent -or $parent -eq $probe) { break }
        $probe = $parent
    }
    return $null
}

function Find-Python311 {
    param([string]$Root)
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($env:MODEL_LAB_PY311) { $candidates.Add($env:MODEL_LAB_PY311) }
    if ($Root) { $candidates.Add((Join-Path $Root '.venv-311\Scripts\python.exe')) }
    foreach ($name in @('python3.11', 'python')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { $candidates.Add($command.Source) }
    }
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        try {
            $version = & $candidate -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
        } catch { continue }
        if ($version -eq '3.11') { return (Resolve-Path -LiteralPath $candidate).Path }
    }
    return $null
}

function Get-PythonAny {
    param([string]$Root)
    $tried = New-Object System.Collections.Generic.List[string]
    if ($Root) { $tried.Add((Join-Path $Root '.venv-311\Scripts\python.exe')) }
    if ($env:MODEL_LAB_PY311) { $tried.Add($env:MODEL_LAB_PY311) }
    foreach ($name in @('python3.11', 'python3', 'python')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { $tried.Add($command.Source) }
    }
    foreach ($candidate in $tried) {
        if (Test-Path -LiteralPath $candidate) { return (Resolve-Path -LiteralPath $candidate).Path }
    }
    return $null
}

function Invoke-Probe {
    param(
        [string]$Python,
        [string]$Script,
        [string[]]$Arguments,
        [string]$LogFile
    )
    Write-Host ""
    Write-Host ("=" * 78) -ForegroundColor DarkGray
    Write-Host ("RUN  {0}" -f $Script) -ForegroundColor Cyan
    Write-Host ("     {0} {1}" -f $Python, (($Arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' ')) -ForegroundColor DarkGray
    Write-Host ("=" * 78) -ForegroundColor DarkGray
    $output = & $Python $Script @Arguments 2>&1
    $code = $LASTEXITCODE
    # Write the log as UTF-8 explicitly: the default redirection on Windows
    # PowerShell 5.1 is UTF-16LE, which other tools (and plain `type`) read badly.
    foreach ($line in $output) { Write-Host $line }
    try {
        $output | Out-String | Set-Content -LiteralPath $LogFile -Encoding utf8 -ErrorAction Stop
    } catch {
        Write-Host ("WARNING: could not write {0}: {1}" -f $LogFile, $_.Exception.Message) -ForegroundColor Yellow
    }
    return $code
}

function Test-WritableDirectory {
    param([string]$Path)
    try {
        if (-not (Test-Path -LiteralPath $Path)) {
            New-Item -ItemType Directory -Path $Path -Force -ErrorAction Stop | Out-Null
        }
        $probe = Join-Path $Path ('.write-test-{0}' -f ([guid]::NewGuid().ToString('N').Substring(0, 6)))
        Set-Content -LiteralPath $probe -Value 'ok' -Encoding ascii -ErrorAction Stop
        Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
        return $true
    } catch {
        return $false
    }
}

$repoRoot = Resolve-RepoRoot -Hint $RepoRoot
$pathProbe = Join-Path $here 'iot_path_probe.py'
if (-not (Test-Path -LiteralPath $pathProbe)) {
    throw "iot_path_probe.py not found next to this script ($here)."
}

$logDir = if ($OutRoot) { $OutRoot } else { Join-Path $here 'logs' }
if (-not (Test-WritableDirectory -Path $logDir)) {
    $fallback = Join-Path $env:TEMP ('iot-probe-{0}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    Write-Warning "$logDir is not writable; logging to $fallback instead"
    $logDir = $fallback
    if (-not (Test-WritableDirectory -Path $logDir)) { throw "no writable log directory (last try: $logDir)" }
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

# The company domains must never travel through a local HTTP proxy.
if (-not $RespectProxy) {
    $entries = @()
    foreach ($scope in @($env:NO_PROXY, $env:no_proxy)) {
        if ($scope) { $entries += ($scope -split ',') }
    }
    $entries += @('bewg.net.cn', '.bewg.net.cn')
    $clean = ($entries | Where-Object { $_ -and $_.Trim() } | ForEach-Object { $_.Trim() } | Select-Object -Unique) -join ','
    $env:NO_PROXY = $clean
    $env:no_proxy = $clean
    if ($BypassProxy) {
        foreach ($name in @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')) {
            Remove-Item "Env:$name" -ErrorAction SilentlyContinue
        }
        Write-Host "proxy variables removed; NO_PROXY=$clean" -ForegroundColor DarkGray
    } else {
        Write-Host "NO_PROXY=$clean" -ForegroundColor DarkGray
    }
}

$anyPython = Get-PythonAny -Root $repoRoot
if (-not $anyPython) { throw 'No Python interpreter found on this machine.' }
Write-Host ("interpreter (path probe) : {0}" -f $anyPython) -ForegroundColor Green

$exitCodes = @()

if (-not $SkipPathProbe) {
    $log = Join-Path $logDir ("01-path-{0}.log" -f $stamp)
    $arguments = @('--json', (Join-Path $logDir ("01-path-{0}.json" -f $stamp)))
    $exitCodes += Invoke-Probe -Python $anyPython -Script $pathProbe -Arguments $arguments -LogFile $log
    Write-Host ("path probe log: {0}" -f $log) -ForegroundColor DarkGray
}

$frameworkDir = $Framework
if (-not $frameworkDir -and $repoRoot) {
    $candidate = Join-Path $repoRoot 'framework'
    if (Test-Path (Join-Path $candidate 'mlops_deploy_iot_armor2_311.py')) { $frameworkDir = $candidate }
}
if (-not $frameworkDir -and (Test-Path (Join-Path $here 'framework\mlops_deploy_iot_armor2_311.py'))) {
    $frameworkDir = Join-Path $here 'framework'
}

# A relative -Framework / -Zip is relative to whatever directory the operator is
# standing in; the probes run as child processes with a different working
# directory, so pin both to absolute paths before handing them over.
if ($Zip) {
    if (Test-Path -LiteralPath $Zip) {
        $Zip = (Resolve-Path -LiteralPath $Zip).Path
    } else {
        Write-Host ("WARNING: -Zip {0} does not exist." -f $Zip) -ForegroundColor Yellow
    }
}
if ($frameworkDir) {
    if (Test-Path -LiteralPath $frameworkDir) {
        $frameworkDir = (Resolve-Path -LiteralPath $frameworkDir).Path
    } else {
        Write-Host ("WARNING: -Framework {0} does not exist." -f $frameworkDir) -ForegroundColor Yellow
    }
    if (-not (Test-Path -LiteralPath (Join-Path $frameworkDir 'mlops_deploy_iot_armor2_311.py'))) {
        Write-Host ("  no mlops_deploy_iot_armor2_311.py in {0}; step 2 would report MODULE_NOT_FOUND." -f $frameworkDir) -ForegroundColor DarkGray
    }
}

if (-not $FactoryCode -or -not $Point) {
    Write-Host ""
    Write-Host "Step 2 (real fetch) skipped: pass -FactoryCode and -Point to run it." -ForegroundColor Yellow
    Write-Host "  example: .\run_probe.ps1 -FactoryCode <CODE> -Point <POINT_CODE>" -ForegroundColor DarkGray
} else {
    $python311 = Find-Python311 -Root $repoRoot
    if (-not $python311) {
        Write-Host ""
        Write-Host "Python 3.11 not found; the company provider is built for cp311 and cannot be imported." -ForegroundColor Yellow
        Write-Host "  install Python 3.11 or create .venv-311, or set MODEL_LAB_PY311 to a python.exe." -ForegroundColor DarkGray
    } else {
        Write-Host ("interpreter (fetch probe): {0}" -f $python311) -ForegroundColor Green
        $fetchProbe = Join-Path $here 'iot_fetch_probe.py'
        if (-not (Test-Path -LiteralPath $fetchProbe)) {
            Write-Host "iot_fetch_probe.py not found next to this script; skipping step 2." -ForegroundColor Yellow
        } else {
            $arguments = New-Object System.Collections.Generic.List[string]
            if ($frameworkDir) { $arguments.Add('--framework'); $arguments.Add($frameworkDir) }
            $arguments.Add('--factory-code'); $arguments.Add($FactoryCode)
            foreach ($code in $Point) { $arguments.Add('--point'); $arguments.Add($code) }
            if ($Start) { $arguments.Add('--start'); $arguments.Add($Start) }
            if ($RollingWindow) { $arguments.Add('--rolling-window'); $arguments.Add($RollingWindow) }
            if ($Interval) { $arguments.Add('--interval'); $arguments.Add($Interval) }
            if ($Zip) { $arguments.Add('--zip'); $arguments.Add($Zip) }
            foreach ($pair in $Map) { $arguments.Add('--map'); $arguments.Add($pair) }
            $arguments.Add('--out'); $arguments.Add((Join-Path $logDir ("02-fetch-{0}.json" -f $stamp)))
            $log = Join-Path $logDir ("02-fetch-{0}.log" -f $stamp)
            $exitCodes += Invoke-Probe -Python $python311 -Script $fetchProbe -Arguments $arguments.ToArray() -LogFile $log
            Write-Host ("fetch probe log: {0}" -f $log) -ForegroundColor DarkGray
        }
    }
}

Write-Host ""
Write-Host "--- SUMMARY ---" -ForegroundColor Cyan
foreach ($file in Get-ChildItem -LiteralPath $logDir -Filter ("*{0}*.log" -f $stamp) -ErrorAction SilentlyContinue) {
    Write-Host ("{0}:" -f $file.Name) -ForegroundColor White
    Select-String -LiteralPath $file.FullName -Pattern 'vpn_required_for_fetch|iot_endpoint_path|classification|rows=|"rows"|WINDOWS_IOT_DIAGNOSTIC|module digest' |
        ForEach-Object { Write-Host ("  {0}" -f $_.Line.Trim()) }
}
Write-Host ""
Write-Host ("logs in {0}" -f $logDir) -ForegroundColor DarkGray
if ($exitCodes -contains 1) { exit 1 } else { exit 0 }
