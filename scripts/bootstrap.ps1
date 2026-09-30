[CmdletBinding()]
param(
    [string]$ModelsRoot,
    [switch]$Update
)

$ErrorActionPreference = "Stop"
$FrameworkRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvDirectory = Join-Path $FrameworkRoot ".venv-311"
$VenvPython = Join-Path $VenvDirectory "Scripts\python.exe"
$Pyproject = Join-Path $FrameworkRoot "pyproject.toml"

function Test-Python311([string]$Python) {
    if ([string]::IsNullOrWhiteSpace($Python)) { return $false }
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { return $false }
    & $Python -I -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" *> $null
    return $LASTEXITCODE -eq 0
}

function Find-Python311 {
    $UvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -ne $UvCommand) {
        $Found = & $UvCommand.Source python find 3.11 2>$null
        if ($LASTEXITCODE -eq 0) {
            $Candidate = @($Found | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Last 1)
            if ($Candidate.Count -gt 0 -and (Test-Python311 ([string]$Candidate[0]).Trim())) {
                return ([string]$Candidate[0]).Trim()
            }
        }
    }

    $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $PyLauncher) {
        $Found = & $PyLauncher.Source -3.11 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $Candidate = @($Found | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Last 1)
            if ($Candidate.Count -gt 0 -and (Test-Python311 ([string]$Candidate[0]).Trim())) {
                return ([string]$Candidate[0]).Trim()
            }
        }
    }

    foreach ($Name in @("python3.11", "python3", "python")) {
        $PythonCommand = Get-Command $Name -ErrorAction SilentlyContinue
        if ($null -ne $PythonCommand -and (Test-Python311 $PythonCommand.Source)) {
            return $PythonCommand.Source
        }
    }
    return $null
}

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Executable"
    }
}

try {
    Write-Host "Local setup plan: verify/create Python 3.11; install framework/test dependencies; save ignored settings; synchronize project skills."
    Write-Host "Framework: $FrameworkRoot | Update: $Update | No model writes or platform upload."
    Push-Location $FrameworkRoot
    try {
        $VenvItem = Get-Item -LiteralPath $VenvDirectory -Force -ErrorAction SilentlyContinue
        if ($null -ne $VenvItem -and ($VenvItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw ".venv-311 is a link or junction; inspect it and choose a regular local environment."
        }

        if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
            if (-not (Test-Python311 $VenvPython)) {
                throw "Existing .venv-311 is not Python 3.11. It was left unchanged."
            }
        } else {
            if ($null -ne $VenvItem) {
                throw "A .venv-311 directory exists without its Python interpreter; inspect it before retrying."
            }
            $BasePython = Find-Python311
            if ($null -eq $BasePython) {
                throw "Python 3.11 was not found through uv or the system Python launchers."
            }
            $UvCommand = Get-Command uv -ErrorAction SilentlyContinue
            if ($null -ne $UvCommand) {
                Invoke-Checked $UvCommand.Source @("venv", "--python", $BasePython, $VenvDirectory)
            } else {
                Invoke-Checked $BasePython @("-m", "venv", $VenvDirectory)
            }
            if (-not (Test-Python311 $VenvPython)) {
                throw "The created .venv-311 interpreter did not verify as Python 3.11."
            }
        }

        $HasUpdateExtra = & $VenvPython -I -c "import sys, tomllib; data=tomllib.load(open(sys.argv[1], 'rb')); print('yes' if 'update' in data.get('project', {}).get('optional-dependencies', {}) else 'no')" $Pyproject
        if ($LASTEXITCODE -ne 0) { throw "Could not inspect optional dependency groups in pyproject.toml." }
        $Extra = if ([string]$HasUpdateExtra -eq "yes") { ".[test,update]" } else { ".[test]" }
        $InstallArguments = @("pip", "install")
        if ($Update) { $InstallArguments += "--upgrade" }
        $InstallArguments += @("--python", $VenvPython, "--editable", $Extra)
        $UvCommand = Get-Command uv -ErrorAction SilentlyContinue
        if ($null -ne $UvCommand) {
            Invoke-Checked $UvCommand.Source $InstallArguments
        } else {
            $PipArguments = @("-m", "pip", "install")
            if ($Update) { $PipArguments += "--upgrade" }
            $PipArguments += @("--editable", $Extra)
            Invoke-Checked $VenvPython $PipArguments
        }

        $SetupArguments = @("-I", "-m", "model_lab", "setup")
        if (-not [string]::IsNullOrWhiteSpace($ModelsRoot)) {
            $SetupArguments += @("--models-root", $ModelsRoot)
        }
        $SetupArguments += "--apply"
        if ($Update) { $SetupArguments += "--update" }
        Invoke-Checked $VenvPython $SetupArguments
    } finally {
        Pop-Location
    }
} catch {
    [Console]::Error.WriteLine("model-lab bootstrap: {0}", $_.Exception.Message)
    exit 1
}
