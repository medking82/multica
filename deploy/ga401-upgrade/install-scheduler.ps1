[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$SourceDirectory,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$SourceCommit,
    [switch]$Register
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$taskName = 'Multica-GA401-UpstreamUpdate'
$owner = 'Multica GA401 committed upstream updater v1'
$repo = (Resolve-Path -LiteralPath $SourceDirectory).Path
if ($repo -ne 'C:\github\multica-ga401-upgrade-0439') { throw 'Unexpected scheduler checkout' }
$root = Join-Path $env:LOCALAPPDATA 'MulticaAutoUpdate'
$python = 'C:\Users\Marck\AppData\Local\Programs\Python\Python312\python.exe'
$runner = 'C:\Users\Marck\.codex\bin\invoke-hidden.ps1'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -ne $owner) { throw 'Task name belongs to another owner' }
if ($existing -and $existing.State -eq 'Running') { throw 'Existing cycle is running' }
& $runner -FilePath $python -ArgumentList @('-B', (Join-Path $PSScriptRoot 'install_sources.py'), '--repo', $repo, '--commit', $SourceCommit, '--root', $root) -WorkingDirectory $repo
if ($LASTEXITCODE -ne 0) { throw 'Committed source installation failed' }
$version = Join-Path (Join-Path $root 'versions') $SourceCommit
$manifest = Join-Path $version 'manifest.json'
$manifestHash = (Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash
$state = Join-Path $root 'state'
$logs = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $state, $logs | Out-Null
$log = Join-Path $logs 'cycle.log'
$wrapper = Join-Path $root 'run-cycle.ps1'
$body = @"
`$ErrorActionPreference = 'Stop'
try {
    if ((Get-FileHash -LiteralPath '$manifest' -Algorithm SHA256).Hash -ne '$manifestHash') { throw 'Installed manifest integrity mismatch' }
    `$m = Get-Content -LiteralPath '$manifest' -Raw | ConvertFrom-Json
    if (`$m.commit -ne '$SourceCommit') { throw 'Installed commit mismatch' }
    foreach (`$name in @('cycle.py','discover.py','upgrade.py')) {
        `$hash = (Get-FileHash -LiteralPath (Join-Path '$version' `$name) -Algorithm SHA256).Hash.ToLowerInvariant()
        if (`$hash -ne `$m.files.`$name) { throw 'Installed source integrity mismatch' }
    }
    & '$runner' -FilePath '$python' -ArgumentList @('-B','$(Join-Path $version 'cycle.py')','--repo','$repo','--state-root','$state') -WorkingDirectory '$repo' *>> '$log'
    exit `$LASTEXITCODE
} catch {
    Add-Content -LiteralPath '$log' -Value (('NEEDS ATTENTION ' + (Get-Date).ToUniversalTime().ToString('o')) + ' ' + `$_.Exception.Message)
    exit 1
}
"@
[IO.File]::WriteAllText($wrapper, $body, [Text.UTF8Encoding]::new($false))
if ($Register) {
    $action = New-ScheduledTaskAction -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -File `"$wrapper`""
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours 6)
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 4) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Description $owner -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
}
[ordered]@{task=$taskName; source_commit=$SourceCommit; root=$root; registered=[bool]$Register} | ConvertTo-Json
