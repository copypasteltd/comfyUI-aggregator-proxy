param([ValidateSet('All','Python','Frontend','Docs','Sub2API','Sub2APILedger','Sub2APIProvider','Sub2APINative','Sub2APINativeCatalog','Sub2APINativeRetry')][string]$Suite='All')
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $taskRoot
$env:UV_CACHE_DIR = Join-Path $taskRoot '.local/uv-cache'
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
function Invoke-Checked([string]$Executable, [string[]]$TaskArguments) {
    & $Executable @TaskArguments
    if ($LASTEXITCODE -ne 0) { throw ('Check failed: '+$Executable+' (exit '+$LASTEXITCODE+')') }
}
if ($Suite -in @('All','Python')) {
    Invoke-Checked (Join-Path $taskRoot '.venv\Scripts\uv.exe') @('lock','--check')
    Invoke-Checked (Join-Path $taskRoot '.venv\Scripts\ruff.exe') @('check','packages','apps','scripts','tests','migrations')
    Invoke-Checked (Join-Path $taskRoot '.venv\Scripts\mypy.exe') @('packages','apps')
    Invoke-Checked (Join-Path $taskRoot '.venv\Scripts\pytest.exe') @('-q','tests','--tb=short')
}
if ($Suite -in @('All','Frontend')) {
    $taskNodeDirectory = Join-Path $taskRoot '.local\toolchains\node-24.22.1\node-v24.22.1-win-x64'
    $env:PATH = $taskNodeDirectory+';'+$env:PATH
    Invoke-Checked $taskPython @('scripts/export-openapi.py')
    Push-Location -LiteralPath 'apps\admin_web'
    try {
        Invoke-Checked (Join-Path $taskNodeDirectory 'npm.cmd') @('run','generate:api')
        Invoke-Checked (Join-Path $taskNodeDirectory 'npm.cmd') @('run','build')
    } finally { Pop-Location }
}
if ($Suite -in @('All','Docs')) {
    Push-Location -LiteralPath (Split-Path $taskRoot -Parent)
    try { Invoke-Checked $taskPython @('-X','utf8','docs/tools/verify_docs.py') } finally { Pop-Location }
}
if ($Suite -in @('All','Sub2API')) {
    Invoke-Checked $taskPython @('scripts/test-sub2api-client.py')
}
if ($Suite -in @('All','Sub2APILedger')) {
    Invoke-Checked $taskPython @('scripts/test-sub2api-ledger.py')
}
if ($Suite -in @('All','Sub2APIProvider')) {
    Invoke-Checked $taskPython @('scripts/build-sub2api-provider.py')
}
if ($Suite -in @('All','Sub2APINative')) {
    Invoke-Checked $taskPython @('scripts/test-sub2api-native.py')
}
if ($Suite -in @('All','Sub2APINativeCatalog')) {
    Invoke-Checked $taskPython @('scripts/test-sub2api-native.py','--managed-catalog')
}

if ($Suite -in @('All','Sub2APINativeRetry')) {
    Invoke-Checked $taskPython @('scripts/test-sub2api-native.py','--confirmed-oom')
}
