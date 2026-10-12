# SmartLogistics developer CLI for Windows (PowerShell).
#   .\dev.ps1 help      .\dev.ps1 infra up      .\dev.ps1 start
# All logic lives in scripts/dev.py, shared with dev.sh (macOS / Linux).
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "uv is required: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
}
# Keep the workspace virtualenv in step with uv.lock before every command: it
# installs every service on the first run, repairs a stale .venv (for example
# one left by the old monolith), and is a no-op taking milliseconds otherwise.
uv sync --all-packages --quiet
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$cliArgs = $args
if ($cliArgs.Count -eq 0 -or $cliArgs[0] -eq "help") { $cliArgs = @("--help") }
uv run --no-sync python scripts/dev.py @cliArgs
exit $LASTEXITCODE
