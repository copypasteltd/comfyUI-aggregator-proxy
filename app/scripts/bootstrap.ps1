param([switch]$WithPostgresToolchain, [switch]$WithBuildTools)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $taskRoot
$env:UV_CACHE_DIR = Join-Path $taskRoot '.local/uv-cache'
$taskLock = Get-Content -LiteralPath 'config/native-toolchains.lock.json' -Raw -Encoding UTF8 | ConvertFrom-Json
function Invoke-Checked([string]$Executable, [string[]]$TaskArguments) {
    & $Executable @TaskArguments
    if ($LASTEXITCODE -ne 0) { throw ('Failed command: '+$Executable+' (exit '+$LASTEXITCODE+')') }
}
function Install-PinnedArchive([string]$Source, [string]$Digest, [string]$Archive, [string]$Destination) {
    if (-not (Test-Path -LiteralPath $Archive)) {
        $taskPartial = $Archive+'.partial'
        Invoke-WebRequest -Uri $Source -OutFile $taskPartial -TimeoutSec 300
        if ((Get-FileHash -LiteralPath $taskPartial -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Digest) { throw 'Downloaded archive hash mismatch; extraction refused.' }
        Move-Item -LiteralPath $taskPartial -Destination $Archive
    }
    if ((Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Digest) { throw 'Pinned archive hash mismatch.' }
    if (-not (Test-Path -LiteralPath $Destination)) { Expand-Archive -LiteralPath $Archive -DestinationPath $Destination }
}
if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) { throw 'This bootstrap entry supports native Windows. Linux deployment has a separate runbook.' }
$taskPython = Join-Path $taskRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    $taskBasePython = (Get-Command python.exe -ErrorAction Stop).Source
    Invoke-Checked $taskBasePython @('-c','import sys; assert sys.version_info[:2] == (3,13), "Native Python 3.13 is required"')
    Invoke-Checked $taskBasePython @('-m','venv','.venv')
}
if (-not (Test-Path -LiteralPath '.venv/Scripts/uv.exe')) { Invoke-Checked $taskPython @('-m','pip','install','uv==0.13.5') }
Invoke-Checked (Join-Path $taskRoot '.venv/Scripts/uv.exe') @('lock','--check')
if ($WithBuildTools) {
    Invoke-Checked (Join-Path $taskRoot '.venv/Scripts/uv.exe') @('sync','--frozen','--group','release-build')
} else {
    Invoke-Checked (Join-Path $taskRoot '.venv/Scripts/uv.exe') @('sync','--frozen')
}
New-Item -ItemType Directory -Path '.local/toolchains' -Force | Out-Null
Install-PinnedArchive $taskLock.node.source $taskLock.node.sha256 '.local/toolchains/node-24.22.1.zip' '.local/toolchains/node-24.22.1'
$taskNode = Join-Path $taskRoot '.local/toolchains/node-24.22.1/node-v24.22.1-win-x64'
$env:PATH = $taskNode+';'+$env:PATH
Push-Location -LiteralPath 'apps/admin_web'
try { Invoke-Checked (Join-Path $taskNode 'npm.cmd') @('ci','--ignore-scripts','--audit=false','--fund=false') } finally { Pop-Location }
if ($WithPostgresToolchain) {
    Install-PinnedArchive $taskLock.postgresql.source $taskLock.postgresql.sha256 '.local/toolchains/postgres-18.6.zip' '.local/toolchains/postgres-18.6'
}
Write-Output 'Pinned native dependencies installed. Database initialization and migrations remain separate commands.'
