$repoRoot = (git rev-parse --show-toplevel)
if (-not $repoRoot) { exit 0 }
$hookScript = Join-Path $repoRoot '.codex/hooks/model_context.py'
$localPython = Join-Path $repoRoot '.venv-311/Scripts/python.exe'
if (Test-Path -LiteralPath $localPython -PathType Leaf) {
    & $localPython $hookScript
} else {
    exit 0
}
exit $LASTEXITCODE
