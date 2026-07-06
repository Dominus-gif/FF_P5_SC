# Stops and removes the background stock checker task.
$taskName = "PS5 Stock Checker"
Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Get-Process python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine -like "*checker.py*" } |
    Stop-Process -Force
Write-Host "Task '$taskName' removed and checker stopped."
