# Runs the stock checker in an endless loop, logging to checker.log.
# Launched hidden by the "PS5 Stock Checker" scheduled task at logon.
Set-Location $PSScriptRoot

# Keep the log from growing forever: start fresh if over 5 MB
$log = Join-Path $PSScriptRoot "checker.log"
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 5MB)) {
    Remove-Item $log -Force
}

"=== Checker starting at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File $log -Append -Encoding utf8

# -X utf8 so rupee symbols print fine; output appended to checker.log
python -X utf8 checker.py --loop 120 --stock-only *>> $log
