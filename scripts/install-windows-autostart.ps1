<#
.SYNOPSIS
    Registers a Windows Startup-folder entry that starts the codextender
    proxy at user logon, hidden (no console window).

.DESCRIPTION
    Runs the venv's codextender.exe (relative to this script's repo root, so
    it works regardless of where the repo is checked out) with the given
    --port/--model args, via a VBS wrapper (WScript.Shell.Run with the
    visibility flag 0) that redirects stdout/stderr to a log file.

    Deliberately NOT a Scheduled Task (the previous shape of this script):
    on an AzureAD/corporate-managed machine, Register-ScheduledTask can
    require elevation to register, and even once registered the task can
    fail to actually launch a process (LastTaskResult=1, no process, no log
    line — confirmed live, 2026-09-26) while the exact same binary launches
    fine from an interactive PowerShell session. Task Scheduler policy
    enforcement on managed devices does not distinguish "your own task,
    launched at your own logon" from anything else it restricts. A Startup-
    folder shortcut runs in the same interactive logon session as a script
    you run by hand — the one path already proven to work — so it sidesteps
    that restriction entirely. This mirrors shipwright-webui's own
    scripts/install-windows.ps1, which uses the identical VBS-in-Startup-
    folder pattern for the same reason.

    Idempotent: re-running overwrites the existing shortcut/launcher rather
    than erroring or duplicating it.

.PARAMETER ModelArgs
    One or more "SLUG[:ALIAS]" pairs, same shape as codextender's own
    --model flag. Default: a single gpt-6-sol:sol.

.PARAMETER Port
    Port the proxy listens on. Default: 4000.

.PARAMETER LogDir
    Where stdout/stderr get appended. Default: %LOCALAPPDATA%\codextender\logs.
    Not rotated — delete/trim the file yourself if it grows large.

.PARAMETER Uninstall
    Removes the Startup-folder shortcut and VBS launcher. Does not stop an
    already-running proxy process.

.EXAMPLE
    .\install-windows-autostart.ps1
    Installs the startup entry with the default gpt-6-sol:sol model on port 4000.

.EXAMPLE
    .\install-windows-autostart.ps1 -ModelArgs "gpt-6-sol:sol","gpt-6-astra:astra" -Port 4001
    Installs the startup entry exposing both models on port 4001.

.NOTES
    Run this yourself — it registers persistent, boot-time login state, which
    is exactly the kind of change codextender's own docs ask you to run
    rather than have an agent run for you.

    To remove: .\install-windows-autostart.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string[]] $ModelArgs = @("gpt-6-sol:sol"),
    [int] $Port = 4000,
    [string] $LogDir = (Join-Path $env:LOCALAPPDATA "codextender\logs"),
    [switch] $Uninstall
)

$ErrorActionPreference = "Stop"

$StartupFolder = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $StartupFolder "Codextender.lnk"
$VbsPath = Join-Path $env:LOCALAPPDATA "codextender\start-codextender.vbs"
$VbsDir = Split-Path -Parent $VbsPath

if ($Uninstall) {
    Write-Host "Removing Codextender startup entry..." -ForegroundColor Yellow
    if (Test-Path $ShortcutPath) {
        Remove-Item $ShortcutPath -Force
        Write-Host "  Removed: $ShortcutPath" -ForegroundColor Green
    }
    if (Test-Path $VbsPath) {
        Remove-Item $VbsPath -Force
        Write-Host "  Removed: $VbsPath" -ForegroundColor Green
    }
    Write-Host "Uninstalled. codextender will no longer start on login." -ForegroundColor Green
    exit 0
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$exePath = Join-Path $repoRoot ".venv\Scripts\codextender.exe"

if (-not (Test-Path $exePath)) {
    throw "codextender.exe not found at '$exePath'. Create the venv and " +
        "'pip install -e .' first (see README.md Install)."
}

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}
if (-not (Test-Path $VbsDir)) {
    New-Item -ItemType Directory -Path $VbsDir -Force | Out-Null
}
$logFile = Join-Path $LogDir "codextender.log"

$modelFlags = ($ModelArgs | ForEach-Object { "--model `"$_`"" }) -join " "
$innerCommand = "`"$exePath`" --port $Port $modelFlags >> `"$logFile`" 2>&1"

# VBS wrapper: WScript.Shell.Run with the visibility flag 0 starts the process
# with no console window, and (unlike a Scheduled Task action) no extra layer
# for a managed-device policy to distinguish from "you double-clicked this
# yourself." Same pattern, same rationale, as shipwright-webui's
# install-windows.ps1.
#
# $innerCommand's own double-quotes (needed for cmd.exe to handle a spaced
# path) must be DOUBLED before landing inside the VBS string literal below —
# VBS escapes an embedded quote as "" — or wscript.exe fails to parse the
# generated .vbs at all ("Expected end of statement", confirmed live
# 2026-09-26: a bare, unescaped quote from $innerCommand closed the VBS string
# literal early). $innerCommand itself stays single-quoted for the Write-Host
# summary below, where cmd-style quoting is what a human actually wants to see.
$vbsSafeInnerCommand = $innerCommand -replace '"', '""'
$escapedServerDir = $repoRoot -replace '\\', '\\'
$vbsContent = @"
' codextender — Background Proxy Launcher
' Auto-generated by install-windows-autostart.ps1
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "$escapedServerDir"
WshShell.Run "cmd /c $vbsSafeInnerCommand", 0, False
"@

# UTF-16LE (-Encoding Unicode): wscript.exe reads it natively, and unlike
# -Encoding ASCII it does not map non-ASCII path characters to "?", which
# would silently corrupt the baked-in $exePath/$logFile and break autostart.
Set-Content -Path $VbsPath -Value $vbsContent -Encoding Unicode

# Sanity check: re-read the launcher and confirm the embedded repo path
# survived the encoding round-trip, same guard as install-windows.ps1 uses.
$vbsWritten = Get-Content -Path $VbsPath -Raw -Encoding Unicode
if (-not $vbsWritten.Contains($escapedServerDir)) {
    Remove-Item $VbsPath -Force -ErrorAction SilentlyContinue
    throw "VBS launcher path failed to round-trip after writing — the install " +
        "path may contain characters the file encoding cannot represent. " +
        "Path: $repoRoot. Startup entry NOT configured."
}

$WScriptShell = New-Object -ComObject WScript.Shell
$Shortcut = $WScriptShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $VbsPath
$Shortcut.WorkingDirectory = $repoRoot
$Shortcut.Description = "Starts the codextender proxy (Codex-plan models for Claude Code) at logon."
$Shortcut.Save()

Write-Host "Installed Codextender startup entry."
Write-Host "  Command:  $exePath --port $Port $modelFlags"
Write-Host "  Shortcut: $ShortcutPath"
Write-Host "  Launcher: $VbsPath"
Write-Host "  Log:      $logFile"
Write-Host "  Starts at your next logon, or run now with:"
Write-Host "    wscript.exe `"$VbsPath`""
