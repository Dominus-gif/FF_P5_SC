# Registers a scheduled task that starts the stock checker, hidden,
# every time you log into Windows. Run once:  .\install_task.ps1
# Remove with:  .\uninstall_task.ps1

$taskName = "PS5 Stock Checker"
$scriptPath = Join-Path $PSScriptRoot "run_checker.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Checks PS5 stock every 2 min, alerts on Telegram" -Force | Out-Null

Start-ScheduledTask -TaskName $taskName
Write-Host "Installed and started task '$taskName'."
Write-Host "It now runs hidden in the background and auto-starts at every logon."
Write-Host "Watch it live with:  Get-Content checker.log -Wait -Tail 20"
