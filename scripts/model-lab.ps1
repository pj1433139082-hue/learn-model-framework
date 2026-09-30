$ErrorActionPreference = "Stop"
$FrameworkRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $FrameworkRoot ".venv-311\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    [Console]::Error.WriteLine("model-lab: run scripts/bootstrap.ps1 first to initialize .venv-311")
    exit 2
}

& $Python -I -m model_lab @args
exit $LASTEXITCODE
