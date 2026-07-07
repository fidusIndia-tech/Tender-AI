param(
  [string]$PythonExe = "",
  [string]$AgentDir = $PSScriptRoot,
  [string[]]$RunAt = @("09:15AM"),
  [string]$TaskName = "Tender AI GeM Result Watcher Agent",
  [string[]]$RecheckAt = @("11:00PM"),
  [string]$RecheckTaskName = "Tender AI GeM Result Recheck and Repair"
)

$AgentDir = (Resolve-Path -LiteralPath $AgentDir).Path
if (-not $PythonExe) {
  $venvPython = Join-Path $AgentDir ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPython) {
    $PythonExe = $venvPython
  } else {
    $PythonExe = "python"
  }
}

$watcher = Join-Path $AgentDir "watcher.py"
$action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$watcher`" --run-now" -WorkingDirectory $AgentDir
$triggers = @()
foreach ($time in $RunAt) {
  $triggers += New-ScheduledTaskTrigger -Daily -At $time
}
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $triggers `
  -Settings $settings `
  -Description "Checks GeM tender results from the office PC and updates Tender AI." `
  -Force

# Second daily task: recheck every ended tender and repair stale statuses.
# This corrects old false "result available" statuses and, for tenders that
# genuinely have a result, ingests the real evaluation rows (which populate the
# tender's + expand and fire the "result is live" notification). It runs with
# --apply so the repairs are saved.
$recheckAction = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$watcher`" --recheck-and-fix-statuses --apply" -WorkingDirectory $AgentDir
$recheckTriggers = @()
foreach ($time in $RecheckAt) {
  $recheckTriggers += New-ScheduledTaskTrigger -Daily -At $time
}

Register-ScheduledTask `
  -TaskName $RecheckTaskName `
  -Action $recheckAction `
  -Trigger $recheckTriggers `
  -Settings $settings `
  -Description "Rechecks ended GeM tenders, repairs stale result statuses, and ingests published evaluation details." `
  -Force

Write-Host "Scheduled task created: $TaskName"
Write-Host "Scheduled task created: $RecheckTaskName"
Write-Host "Python: $PythonExe"
Write-Host "AgentDir: $AgentDir"
Write-Host "Run-now times: $($RunAt -join ', ')"
Write-Host "Recheck times: $($RecheckAt -join ', ')"
