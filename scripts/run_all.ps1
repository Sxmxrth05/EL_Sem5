# Windows equivalent of `make all`: tests, default experiment, then start the service.
#   powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1 [-SkipServe]
param([switch]$SkipServe, [string]$Config = "configs/default.yaml", [int]$Port = 8000)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

Write-Host "== lint"
& $py -m ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "== tests"
& $py -m pytest -rs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "== experiment"
if (Test-Path "eval\run_experiment.py") {
    & $py -m eval.run_experiment --config $Config
} else {
    Write-Host "eval/run_experiment.py not merged yet (Person 3). Running parity summary instead."
    & $py -m tools.parity
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $SkipServe) {
    Write-Host "== service on http://127.0.0.1:$Port/"
    & $py -m uvicorn service.api:app --port $Port
}
