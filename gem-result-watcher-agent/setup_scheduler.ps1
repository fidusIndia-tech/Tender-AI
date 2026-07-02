param(
  [string]$PythonExe = "python",
  [string]$AgentDir = $PSScriptRoot
)

$watcher = Join-Path $AgentDir "watcher.py"
$action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$watcher`" --run-now" -WorkingDirectory $AgentDir
$triggerMorning = New-ScheduledTaskTrigger -Daily -At 10:00AM
$triggerEvening = New-ScheduledTaskTrigger -Daily -At 5:00PM
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask `
  -TaskName "Tender AI GeM Result Watcher Agent" `
  -Action $action `
  -Trigger @($triggerMorning, $triggerEvening) `
  -Settings $settings `
  -Description "Checks GeM tender results from the office PC and updates Tender AI." `
  -Force

Write-Host "Scheduled task created: Tender AI GeM Result Watcher Agent"
