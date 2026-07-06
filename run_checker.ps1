# Runs the stock checker in an endless loop, logging to checker.log.
# Launched hidden by the "PS5 Stock Checker" scheduled task at logon.
Set-Location $PSScriptRoot

# Keep the log from growing forever: start fresh if over 5 MB
$log = Join-Path $PSScriptRoot "checker.log"
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 5MB)) {
    Remove-Item $log -Force
}

[System.IO.File]::AppendAllText($log, "=== Checker starting at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===`r`n")

# cmd handles the redirect so python's UTF-8 output lands in the file
# byte-for-byte (PowerShell's own >> would re-encode it as UTF-16 and
# corrupt the log). -u = unbuffered, -X utf8 = rupee signs print fine.
cmd /c "python -X utf8 -u checker.py --loop 120 --stock-only >> checker.log 2>&1"
