<#
.SYNOPSIS
    Registers a Windows Scheduled Task that starts the codextender proxy at
    user logon, restarting it automatically if it crashes.

.DESCRIPTION
    Runs the venv's codextender.exe (relative to this script's repo root, so
    it works regardless of where the repo is checked out) with the given
    --port/--model args, wrapped in cmd.exe so stdout/stderr can be
    redirected to a log file (Scheduled Task actions have no native
    redirection).

    "Run only when user is logged on" (not a service, no stored service
    credentials) — this is a per-user dev-machine convenience, not a
    server deployment.

    Idempotent: re-running replaces the existing task of the same name
    rather than erroring or duplicating it.

.PARAMETER ModelArgs
    One or more "SLUG[:ALIAS]" pairs, same shape as codextender's own
    --model flag. Default: a single gpt-6-sol:sol.

.PARAMETER Port
    Port the proxy listens on. Default: 4000.

.PARAMETER TaskName
    Scheduled Task name. Default: "Codextender".

.PARAMETER LogDir
    Where stdout/stderr get appended. Default: %LOCALAPPDATA%\codextender\logs.
    Not rotated — delete/trim the file yourself if it grows large.

.EXAMPLE
    .\install-windows-autostart.ps1
    Registers the task with the default gpt-6-sol:sol model on port 4000.

.EXAMPLE
    .\install-windows-autostart.ps1 -ModelArgs "gpt-6-sol:sol","gpt-6-astra:astra" -Port 4001
    Registers the task exposing both models on port 4001.

.NOTES
    Run this yourself — it registers persistent, boot-time system state
    (a Scheduled Task), which is exactly the kind of change codextender's
    own docs ask you to run rather than have an agent run for you.

    To remove: Unregister-ScheduledTask -TaskName "Codextender" -Confirm:$false
#>
[CmdletBinding()]
param(
    [string[]] $ModelArgs = @("gpt-6-sol:sol"),
    [int] $Port = 4000,
    [string] $TaskName = "Codextender",
    [string] $LogDir = (Join-Path $env:LOCALAPPDATA "codextender\logs")
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$exePath = Join-Path $repoRoot ".venv\Scripts\codextender.exe"

if (-not (Test-Path $exePath)) {
    throw "codextender.exe not found at '$exePath'. Create the venv and " +
        "'pip install -e .' first (see README.md Install)."
}

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}
$logFile = Join-Path $LogDir "codextender.log"

$modelFlags = ($ModelArgs | ForEach-Object { "--model `"$_`"" }) -join " "
$innerCommand = "`"$exePath`" --port $Port $modelFlags >> `"$logFile`" 2>&1"

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $innerCommand"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Starts the codextender proxy (Codex-plan models for Claude Code) at logon." `
    | Out-Null

Write-Host "Registered Scheduled Task '$TaskName'."
Write-Host "  Command: $exePath --port $Port $modelFlags"
Write-Host "  Log:     $logFile"
Write-Host "  Starts at your next logon, or run now with:"
Write-Host "    Start-ScheduledTask -TaskName '$TaskName'"
