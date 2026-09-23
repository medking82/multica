function Get-MulticaSchedulerRoot {
    param([Parameter(Mandatory)]$Task)
    if ($Task.Description -ne 'Multica GA401 committed upstream updater v1' -or @($Task.Actions).Count -ne 1) { throw 'Unexpected task owner or action count' }
    $action = $Task.Actions[0]
    if ([IO.Path]::GetFileName($action.Execute) -ieq 'pwsh.exe' -and $action.Arguments -match '-File\s+"([^"]+\\run-cycle\.ps1)"\s*$') {
        $root = Split-Path -Parent $Matches[1]
    } elseif ([IO.Path]::GetFileName($action.Execute) -ieq 'wscript.exe' -and $action.Arguments -match '^//B //NoLogo "([^"]+\\run-cycle-hidden\.vbs)" "[^"]+\\pwsh\.exe" "([^"]+\\run-cycle\.ps1)"$') {
        $root = Split-Path -Parent $Matches[2]
        if ((Split-Path -Parent $Matches[1]) -ne $root) { throw 'Task launcher and wrapper roots differ' }
    } else { throw 'Unrecognized task entry point' }
    if (-not [IO.Path]::IsPathFullyQualified($root)) { throw 'Task root must be absolute' }
    return [IO.Path]::GetFullPath($root).TrimEnd('\')
}
