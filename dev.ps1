# SmartLogistics developer CLI for Windows (PowerShell).
#   .\dev.ps1 help      .\dev.ps1 infra up      .\dev.ps1 start
# All logic lives in scripts/dev.py, shared with dev.sh (macOS / Linux).
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "uv is required: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
}
# First run: build the workspace virtualenv the CLI runs in.
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    uv sync --all-packages
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
$cliArgs = $args
if ($cliArgs.Count -eq 0 -or $cliArgs[0] -eq "help") { $cliArgs = @("--help") }
uv run --no-sync python scripts/dev.py @cliArgs
exit $LASTEXITCODE
