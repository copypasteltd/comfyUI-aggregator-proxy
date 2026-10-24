param([switch]$SkipRunner)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $taskRoot
$taskMediaDirectory = Join-Path $taskRoot '.local\toolchains\ffmpeg-8.1\bin'
if (-not (Test-Path -LiteralPath (Join-Path $taskMediaDirectory 'ffmpeg.exe'))) { throw 'Verified native media tools are required.' }
$env:PATH = $taskMediaDirectory+';'+$env:PATH
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskRecord = Join-Path $taskRoot '.local\dev-processes.json'
if (Test-Path -LiteralPath $taskRecord) { throw 'Existing process record; run dev-stop.ps1 first.' }
if (-not (Test-Path -LiteralPath 'apps\admin_web\dist\index.html')) { throw 'Admin production build is required before starting.' }
& $taskPython scripts\dev-certificate.py
if ($LASTEXITCODE -ne 0) { throw 'TLS preparation failed.' }
$taskCommands = @(@{ Name='api'; Args=@('-m','uvicorn','control_api.main:create_app','--factory','--host','127.0.0.1','--port','8443','--ssl-keyfile','.local/tls/localhost.key','--ssl-certfile','.local/tls/localhost.crt','--no-proxy-headers','--no-access-log') })
if (-not $SkipRunner) {
    $taskCommands += @{ Name='runner'; Args=@('-m','control_runner.main') }
    $taskCommands += @{ Name='object-gc'; Args=@('-m','control_runner.gc') }
}
$taskProcesses = @()
$taskRoots = @()
try {
    foreach ($taskCommand in $taskCommands) {
        $taskProcess = Start-Process -FilePath $taskPython -ArgumentList $taskCommand.Args -WorkingDirectory $taskRoot -RedirectStandardOutput (Join-Path $taskRoot ('.local\'+$taskCommand.Name+'.out.log')) -RedirectStandardError (Join-Path $taskRoot ('.local\'+$taskCommand.Name+'.err.log')) -PassThru -WindowStyle Hidden
        $taskRoots += @{ Name=$taskCommand.Name; Pid=$taskProcess.Id }
        $taskProcesses += @{ Name=$taskCommand.Name; Pid=$taskProcess.Id; StartTime=$taskProcess.StartTime.ToUniversalTime().ToString('o'); Executable=$taskPython; Depth=0 }
    }
    # The Windows venv redirector creates a native Python child. Record the whole
    # owned tree so stop does not leave the actual API/Runner process behind.
    for ($taskPass=1; $taskPass -le 5; $taskPass++) {
        Start-Sleep -Milliseconds 300
        $taskSnapshot = @(Get-CimInstance Win32_Process)
        foreach ($taskParent in @($taskProcesses)) {
            $taskParentLive = Get-Process -Id $taskParent.Pid -ErrorAction SilentlyContinue
            if (-not $taskParentLive -or $taskParentLive.HasExited -or $taskParentLive.Path -ne $taskParent.Executable -or $taskParentLive.StartTime.ToUniversalTime().Ticks -ne ([datetimeoffset]$taskParent.StartTime).UtcDateTime.Ticks) { continue }
            foreach ($taskChild in @($taskSnapshot | Where-Object { $_.ParentProcessId -eq $taskParent.Pid })) {
                if ($taskProcesses.Pid -contains $taskChild.ProcessId) { continue }
                $taskLive = Get-Process -Id $taskChild.ProcessId -ErrorAction SilentlyContinue
                # ParentProcessId survives its original parent's exit. A reused
                # PID must not make an older unrelated process ours to stop.
                if ($taskLive -and -not $taskLive.HasExited -and $taskLive.StartTime.ToUniversalTime() -ge ([datetimeoffset]$taskParent.StartTime).UtcDateTime) { $taskProcesses += @{ Name=$taskParent.Name+'-child'; Pid=$taskLive.Id; StartTime=$taskLive.StartTime.ToUniversalTime().ToString('o'); Executable=$taskLive.Path; Depth=$taskParent.Depth+1 } }
            }
        }
    }
    ConvertTo-Json -InputObject @($taskProcesses) | Set-Content -LiteralPath $taskRecord -Encoding UTF8
    Write-Output 'Admin/API: https://127.0.0.1:8443/admin/ (project-local development certificate)'
    Write-Output 'Process records and logs: app/.local/; stop with scripts/dev-stop.ps1.'
} catch {
    foreach ($taskProcess in ($taskProcesses | Sort-Object -Property Depth -Descending)) {
        $taskLive = Get-Process -Id $taskProcess.Pid -ErrorAction SilentlyContinue
        if ($taskLive -and -not $taskLive.HasExited -and $taskLive.Path -eq $taskProcess.Executable -and $taskLive.StartTime.ToUniversalTime().Ticks -eq ([datetimeoffset]$taskProcess.StartTime).UtcDateTime.Ticks) { Stop-Process -Id $taskProcess.Pid -ErrorAction SilentlyContinue }
    }
    throw
}
