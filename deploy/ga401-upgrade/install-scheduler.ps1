[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$SourceDirectory,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$SourceCommit,
    [string]$InstallationRoot,
    [switch]$Register
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$taskName = 'Multica-GA401-UpstreamUpdate'
$owner = 'Multica GA401 committed upstream updater v1'
$repo = (Resolve-Path -LiteralPath $SourceDirectory).Path
if ($repo -ne 'C:\github\tools\upstream\multica-ga401') { throw 'Unexpected scheduler checkout' }
$python = 'C:\Users\Marck\AppData\Local\Programs\Python\Python312\python.exe'
$runner = 'C:\Users\Marck\.codex\bin\invoke-hidden.ps1'
$pwsh = 'C:\Program Files\PowerShell\7\pwsh.exe'
$wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'
if (-not (Test-Path -LiteralPath $pwsh -PathType Leaf) -or -not (Test-Path -LiteralPath $wscript -PathType Leaf)) { throw 'Windowless task host is missing' }
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
. (Join-Path $PSScriptRoot 'scheduler-contract.ps1')
$registeredRoot = if ($existing) { Get-MulticaSchedulerRoot -Task $existing } else { $null }
if ($existing -and $existing.State -eq 'Running') { throw 'Existing cycle is running' }
if (-not $PSBoundParameters.ContainsKey('InstallationRoot')) {
    $InstallationRoot = if ($registeredRoot) { $registeredRoot } else { Join-Path $env:LOCALAPPDATA 'MulticaAutoUpdate' }
}
if ([string]::IsNullOrWhiteSpace($InstallationRoot) -or -not [IO.Path]::IsPathFullyQualified($InstallationRoot)) { throw 'Installation path must be absolute' }
$root = [IO.Path]::GetFullPath($InstallationRoot).TrimEnd('\')
if ($root.Contains("'")) { throw 'Installation path cannot contain a single quote' }
if ($registeredRoot -and $root -ne $registeredRoot) { throw 'Installation root differs from the registered task; preserve its existing state' }
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
$launcher = Join-Path $root 'run-cycle-hidden.vbs'
$launcherSource = @'
Option Explicit
Dim args, command, shell
Set args = WScript.Arguments
If args.Count <> 2 Then WScript.Quit 2
Function Quoted(value)
    Quoted = Chr(34) & Replace(value, Chr(34), Chr(34) & Chr(34)) & Chr(34)
End Function
command = Quoted(args(0)) & " -NoProfile -NonInteractive -WindowStyle Hidden -File " & Quoted(args(1))
Set shell = CreateObject("WScript.Shell")
WScript.Quit shell.Run(command, 0, True)
'@
[IO.File]::WriteAllText($launcher, $launcherSource, [Text.Encoding]::ASCII)
if ($Register) {
    $action = New-ScheduledTaskAction -Execute $wscript -Argument "//B //NoLogo `"$launcher`" `"$pwsh`" `"$wrapper`"" -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours 6)
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 4) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Description $owner -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
}
[ordered]@{task=$taskName; source_commit=$SourceCommit; root=$root; registered=[bool]$Register} | ConvertTo-Json
