param(
    [ValidateSet('Bootstrap','Verify')][string]$Action='Verify',
    [ValidatePattern('^[a-z0-9][a-z0-9-]{7,63}$')][string]$RunId=('local-'+[guid]::NewGuid().ToString('N').Substring(0,12)),
    [string]$MediaSource=''
)
$ErrorActionPreference='Stop'
$ciRoot=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $ciRoot
if (-not $IsWindows -or -not [Environment]::Is64BitProcess) { throw 'Native Windows x64 is required' }
if ($Action -eq 'Bootstrap') {
    $ciComputer=Get-CimInstance Win32_ComputerSystem
    if (($ciComputer.Manufacturer+' '+$ciComputer.Model) -match '(?i)vmware|virtual|qemu|kvm|parallels|xen|hvm domu') { throw 'Native physical runner is required' }
}
$ciPython=Join-Path $ciRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $ciPython)) { $ciPython=(Get-Command python.exe -ErrorAction Stop).Source }
$ciArguments=@('-X','utf8','scripts/native_ci.py','--run-id',$RunId)
if ($Action -eq 'Bootstrap') { $ciArguments+='--bootstrap' }
if ($MediaSource) { $ciArguments+=@('--media-source',$MediaSource) }
& $ciPython @ciArguments
exit $LASTEXITCODE
