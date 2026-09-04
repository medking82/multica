$ErrorActionPreference = 'Stop'
$name = 'Multica-GA401-UpstreamUpdate'
$task = Get-ScheduledTask -TaskName $name
$info = Get-ScheduledTaskInfo -TaskName $name
$path = Join-Path $env:LOCALAPPDATA 'MulticaAutoUpdate\state\status.json'
$cycle = if (Test-Path -LiteralPath $path) { Get-Content -LiteralPath $path -Raw | ConvertFrom-Json } else { $null }
[ordered]@{task=$name; state=[string]$task.State; enabled=$task.Settings.Enabled; next_run=$info.NextRunTime.ToString('o'); last_result=$info.LastTaskResult; cycle=$cycle} | ConvertTo-Json -Depth 6
