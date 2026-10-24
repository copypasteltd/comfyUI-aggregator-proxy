$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskRecord = Join-Path $taskRoot '.local\dev-processes.json'
if (-not (Test-Path -LiteralPath $taskRecord)) { Write-Output 'No registered development processes.'; exit 0 }
$taskProcesses = Get-Content -LiteralPath $taskRecord -Raw -Encoding UTF8 | ConvertFrom-Json
# PowerShell 5 returns a JSON array as a single pipeline object. Assign it
# directly so sorting sees individual records instead of the nested array.
foreach ($taskEntry in $taskProcesses) {
    $taskProcess = Get-Process -Id $taskEntry.Pid -ErrorAction SilentlyContinue
    if (-not $taskProcess -or $taskProcess.HasExited) { continue }
    if (-not $taskEntry.Executable -or $taskProcess.Path -ne $taskEntry.Executable -or $taskProcess.StartTime.ToUniversalTime().Ticks -ne ([datetimeoffset]$taskEntry.StartTime).UtcDateTime.Ticks) { throw ('Process identity mismatch for '+$taskEntry.Name+'; no additional process was stopped.') }
}
foreach ($taskEntry in ($taskProcesses | Sort-Object -Property Depth -Descending)) {
    $taskProcess = Get-Process -Id $taskEntry.Pid -ErrorAction SilentlyContinue
    if (-not $taskProcess -or $taskProcess.HasExited) { continue }
    if ($taskProcess.Path -ne $taskEntry.Executable -or $taskProcess.StartTime.ToUniversalTime().Ticks -ne ([datetimeoffset]$taskEntry.StartTime).UtcDateTime.Ticks) { throw ('Process identity mismatch for '+$taskEntry.Name+'; no process was stopped.') }
    try { Stop-Process -Id $taskProcess.Id -ErrorAction Stop }
    catch {
        # A native launcher can exit with its child between lookup and stop.
        # Keep permission/identity failures visible when a process still exists.
        if (Get-Process -Id $taskProcess.Id -ErrorAction SilentlyContinue) { throw }
    }
    Write-Output ('Stopped '+$taskEntry.Name+' PID '+$taskEntry.Pid)
}
Remove-Item -LiteralPath $taskRecord
