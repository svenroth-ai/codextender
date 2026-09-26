# start-codextender.ps1
# ---------------------------------------------------------------------------
# (Re)starts the codextender proxy in the BACKGROUND (no lingering console),
# with a visible status window that closes itself on success.
#
# Same UX as shipwright-webui's scripts/start-server-production.ps1: a window
# opens, shows what's happening, and closes itself after a few seconds once
# the health check confirms the new process actually came up — instead of
# either a silent, feedback-free start (the raw autostart .vbs) or a console
# window that stays open for the process's entire lifetime (running the .exe
# directly).
#
# Requires scripts/install-windows-autostart.ps1 to have been run at least
# once (it generates the .vbs/.cmd launcher this script reuses, so the launch
# command/port/model flags stay in exactly one place).
#
# Run it:  right-click -> "Run with PowerShell", or `.\scripts\start-codextender.ps1`
# Stop it: Get-Process codextender | Stop-Process
# Log:     %LOCALAPPDATA%\codextender\logs\codextender.log
# ---------------------------------------------------------------------------
param(
    [int]$Port = 4000
)

$VbsPath = Join-Path $env:LOCALAPPDATA "codextender\start-codextender.vbs"
$LogFile = Join-Path $env:LOCALAPPDATA "codextender\logs\codextender.log"
$READY_TIMEOUT_MS = 15000
$POLL_MS = 500

Write-Host ''
Write-Host '=== codextender - (re)start proxy (background) ===' -ForegroundColor Cyan
Write-Host ''

if (-not (Test-Path $VbsPath)) {
    Write-Host "ERROR: $VbsPath not found." -ForegroundColor Red
    Write-Host 'Run .\scripts\install-windows-autostart.ps1 once first (it generates the launcher this script reuses).' -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}

# Stop any already-running instance first. A stale instance still listening
# on $Port would otherwise make the health check below pass against the OLD
# process, silently reporting success for code changes that never launched.
$existing = Get-Process -Name 'codextender' -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Stopping existing codextender (PID $($existing.Id -join ', '))..." -ForegroundColor Yellow
    $existing | Stop-Process -Force
    Start-Sleep -Milliseconds 500
}

Write-Host 'Starting codextender in the background...' -ForegroundColor Cyan
wscript.exe $VbsPath

Write-Host 'Waiting for the proxy to come up...' -ForegroundColor Cyan
$up = $false
$deadline = [DateTime]::UtcNow.AddMilliseconds($READY_TIMEOUT_MS)
while ([DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds $POLL_MS
    try {
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health/liveliness" -TimeoutSec 2
        $up = $true
        break
    } catch {
        # Not up yet (or still shutting down the old instance) - keep polling.
    }
}

Write-Host ''
if ($up) {
    $proc = Get-Process -Name 'codextender' -ErrorAction SilentlyContinue
    Write-Host "  OK - codextender runs in the background, no window (pid $($proc.Id), port $Port)." -ForegroundColor Green
    Write-Host "  Log: $LogFile" -ForegroundColor Green
    Write-Host ''
    Write-Host '  This window closes itself in 4s...' -ForegroundColor DarkGray
    Start-Sleep -Seconds 4
} else {
    Write-Host "  codextender did NOT come up on port $Port within $($READY_TIMEOUT_MS / 1000)s." -ForegroundColor Red
    Write-Host "  Log: $LogFile" -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}
