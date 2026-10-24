param([string]$Revision='head', [switch]$Production)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $taskRoot
$taskArguments = @('scripts/migrate.py','--revision',$Revision)
if ($Production) { $taskArguments += '--production' }
& (Join-Path $taskRoot '.venv/Scripts/python.exe') @taskArguments
if ($LASTEXITCODE -ne 0) { throw ('Migration failed (exit '+$LASTEXITCODE+')') }
