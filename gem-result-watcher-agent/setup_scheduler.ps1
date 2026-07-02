param(
  [string]$PythonExe = "",
  [string]$AgentDir = $PSScriptRoot,
  [string[]]$RunAt = @("09:00AM", "03:00PM", "09:00PM"),
  [string]$TaskName = "Tender AI GeM Result Watcher Agent"
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

Write-Host "Scheduled task created: $TaskName"
Write-Host "Python: $PythonExe"
Write-Host "AgentDir: $AgentDir"
Write-Host "Times: $($RunAt -join ', ')"
