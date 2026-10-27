<#
.SYNOPSIS
    Karaoke Party setup on Windows. Idempotent: safe to run again. (Linux: scripts/install.sh)

.DESCRIPTION
    User-level steps (no admin rights):
      1. .env from .env.example (if missing)
      2. uv (pinned version), Python 3.11, the project env (uv sync --frozen: torch cu128, demucs, whisperx, ...)
      3. ffmpeg with the rubberband filter (only if neither FFMPEG_PATH nor the ffmpeg on PATH has it)
    Admin steps (asked first, then run in an elevated window):
      4. firewall rules for the server (TCP PORT) and discovery (UDP DISCOVERY_PORT), Private network profile only
      5. autostart: a Task Scheduler task that starts the tray app at logon

.PARAMETER SkipAdmin
    Only the user-level steps.
.PARAMETER AdminOnly
    Only the admin steps (used by the elevated re-run; needs an elevated PowerShell).
.PARAMETER Uninstall
    Remove the firewall rules and the autostart task (admin). Files and data are left alone.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#>
param(
    [switch]$SkipAdmin,
    [switch]$AdminOnly,
    [switch]$Uninstall,
    [string]$User = "$env:USERDOMAIN\$env:USERNAME"   # who autostart runs as (passed on to the elevated run)
)
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$UvVersion = '0.13.5'
$FfmpegVersion = '4.4.1'
$FfmpegUrl = "https://github.com/GyanD/codexffmpeg/releases/download/$FfmpegVersion/ffmpeg-$FfmpegVersion-essentials_build.zip"
$TaskName = 'Karaoke Party server'
$RuleTcp = 'KaraokeParty-Server-TCP'
$RuleUdp = 'KaraokeParty-Discovery-UDP'
$AdminLog = Join-Path $Root 'data\logs\install-admin.log'

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "    $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

function Read-DotEnv {
    $envFile = Join-Path $Root '.env'
    $values = @{}
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile -Encoding UTF8) {
            if ($line -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$') { $values[$Matches[1]] = $Matches[2].Trim() }
        }
    }
    return $values
}

function Set-DotEnv([string]$key, [string]$value) {
    $envFile = Join-Path $Root '.env'
    $lines = @(Get-Content $envFile -Encoding UTF8)
    $found = $false
    $lines = $lines | ForEach-Object { if ($_ -match "^\s*$key\s*=") { $found = $true; "$key=$value" } else { $_ } }
    if (-not $found) { $lines += "$key=$value" }
    # UTF-8 without BOM: python-dotenv would read a BOM as part of the first key
    [IO.File]::WriteAllLines($envFile, [string[]]$lines, (New-Object Text.UTF8Encoding $false))
}

function Get-Ports {
    $e = Read-DotEnv
    $port = if ($e['PORT']) { [int]$e['PORT'] } else { 8765 }
    $disc = if ($e['DISCOVERY_PORT']) { [int]$e['DISCOVERY_PORT'] } else { 8766 }
    return $port, $disc
}

function Test-Rubberband([string]$ffmpeg) {
    if (-not $ffmpeg -or -not (Test-Path $ffmpeg)) { return $false }
    $filters = & $ffmpeg -hide_banner -filters 2>$null | Out-String
    return $filters -match ' rubberband '
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# --- user-level steps -----------------------------------------------------------------------------------------

function Install-User {
    Step '.env'
    $envFile = Join-Path $Root '.env'
    if (-not (Test-Path $envFile)) {
        Copy-Item (Join-Path $Root '.env.example') $envFile
        Warn '.env created from .env.example: fill in MUSIC_PATH, HOST_PIN and YT_API_KEY.'
    } else { Ok '.env exists' }

    Step "uv $UvVersion"
    $uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    if (-not (Test-Path $uv)) {
        $cmd = Get-Command uv -ErrorAction SilentlyContinue
        if ($cmd) { $uv = $cmd.Source }
    }
    if (-not (Test-Path $uv)) {
        # the official installer, pinned; installs to %USERPROFILE%\.local\bin (no admin rights)
        powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/$UvVersion/install.ps1 | iex"
        $uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
        if (-not (Test-Path $uv)) { throw "uv was not installed to $uv" }
    }
    Ok (& $uv --version)

    Step 'Python 3.11 + project environment (torch cu128, demucs, whisperx; the first run downloads several GB)'
    & $uv python find 3.11 *> $null   # an existing 3.11 (e.g. from python.org) is used as it is
    if ($LASTEXITCODE -ne 0) {
        & $uv python install 3.11
        if ($LASTEXITCODE -ne 0) { throw 'uv python install failed' }
    }
    Push-Location $Root
    try {
        & $uv sync --frozen
        if ($LASTEXITCODE -ne 0) { throw 'uv sync failed' }
    } finally { Pop-Location }
    $py = Join-Path $Root '.venv\Scripts\python.exe'
    $cuda = & $py -c "import torch; print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')" 2>$null
    if ($cuda) { Ok "torch sees the GPU: $cuda (CUDA)" } else {
        Warn 'torch sees no CUDA GPU: songs will be processed on the CPU. That works, but takes several minutes per song.'
        Warn 'With an NVIDIA GPU: install or update the NVIDIA driver (CUDA 12.8 support, version 570 or newer), then run this again.'
    }

    Step 'ffmpeg with rubberband'
    $e = Read-DotEnv
    $onPath = Get-Command ffmpeg -ErrorAction SilentlyContinue
    $ffmpeg = if ($e['FFMPEG_PATH']) { $e['FFMPEG_PATH'] } elseif ($onPath) { $onPath.Source } else { '' }
    if (Test-Rubberband $ffmpeg) {
        Ok "$ffmpeg has rubberband"
    } else {
        if ($ffmpeg) { Warn "$ffmpeg has no rubberband filter; downloading a build that has it" }
        # a private copy, referenced only from .env: PATH is left alone
        $dest = Join-Path $env:LOCALAPPDATA 'karaoke-party'
        $exe = Join-Path $dest "ffmpeg-$FfmpegVersion-essentials_build\bin\ffmpeg.exe"
        if (-not (Test-Rubberband $exe)) {
            New-Item -ItemType Directory -Force $dest | Out-Null
            $zip = Join-Path $dest "ffmpeg-$FfmpegVersion.zip"
            Warn "downloading $FfmpegUrl"
            Invoke-WebRequest -Uri $FfmpegUrl -OutFile $zip -UseBasicParsing
            Expand-Archive -Path $zip -DestinationPath $dest -Force
            Remove-Item $zip
        }
        if (-not (Test-Rubberband $exe)) { throw "$exe has no rubberband filter" }
        Set-DotEnv 'FFMPEG_PATH' $exe
        Ok "FFMPEG_PATH=$exe (written to .env)"
    }
}

# --- admin steps ----------------------------------------------------------------------------------------------

function Set-FirewallRule([string]$name, [string]$display, [string]$protocol, [int]$port) {
    $rule = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
    if ($rule) {
        Set-NetFirewallRule -Name $name -DisplayName $display -Enabled True -Direction Inbound -Action Allow -Profile Private
        Get-NetFirewallRule -Name $name | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -Protocol $protocol -LocalPort $port
        Ok "updated firewall rule: $display (Private only)"
    } else {
        New-NetFirewallRule -Name $name -DisplayName $display -Direction Inbound -Action Allow -Protocol $protocol `
            -LocalPort $port -Profile Private | Out-Null
        Ok "added firewall rule: $display (Private only)"
    }
}

function Install-Admin {
    $port, $disc = Get-Ports
    Step 'Firewall rules (Private network profile only)'
    Set-FirewallRule $RuleTcp "Karaoke Party server (TCP $port)" 'TCP' $port
    Set-FirewallRule $RuleUdp "Karaoke Party discovery (UDP $disc)" 'UDP' $disc

    Step "Autostart: Task Scheduler task '$TaskName' at logon of $User"
    $pythonw = Join-Path $Root '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path $pythonw)) { throw "$pythonw not found: run the user-level steps first" }
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument '-m server.tray' -WorkingDirectory $Root
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
    $principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
        -Settings $settings -Description 'Karaoke Party server + worker with tray icon (scripts/install.ps1)' -Force | Out-Null
    Ok "task registered: $pythonw -m server.tray (runs without admin rights)"
}

function Uninstall-Admin {
    Step 'Removing firewall rules and the autostart task'
    foreach ($name in $RuleTcp, $RuleUdp) {
        if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) { Remove-NetFirewallRule -Name $name; Ok "removed $name" }
    }
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Ok "removed task '$TaskName'"
    }
}

function Invoke-Elevated([string]$mode, [string]$question) {
    if (Test-Admin) {
        if ($mode -eq '-Uninstall') { Uninstall-Admin } else { Install-Admin }
        return
    }
    Write-Host ''
    Write-Host $question -ForegroundColor Yellow
    $answer = Read-Host 'Continue? (y/N)'
    if ($answer -notmatch '^[yY]') { Warn 'skipped; run this script again to do it later'; return }
    New-Item -ItemType Directory -Force (Split-Path $AdminLog) | Out-Null
    if (Test-Path $AdminLog) { Remove-Item $AdminLog }
    $argList = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" $mode -User `"$User`""
    $p = Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden -ArgumentList $argList
    if (Test-Path $AdminLog) { Get-Content $AdminLog | ForEach-Object { Write-Host $_ } }
    if ($p.ExitCode -ne 0) { throw "the elevated step failed (exit code $($p.ExitCode)); see $AdminLog" }
}

# --- main -----------------------------------------------------------------------------------------------------

if ($AdminOnly -or ($Uninstall -and (Test-Admin))) {
    # elevated run: no console to read, so everything also goes to a log the non-elevated run prints
    New-Item -ItemType Directory -Force (Split-Path $AdminLog) | Out-Null
    Start-Transcript -Path $AdminLog -Force | Out-Null
    try {
        if ($Uninstall) { Uninstall-Admin } else { Install-Admin }
    } catch {
        Write-Host "ERROR: $_" -ForegroundColor Red
        Stop-Transcript | Out-Null
        exit 1
    }
    Stop-Transcript | Out-Null
    exit 0
}

if ($Uninstall) {
    Invoke-Elevated '-Uninstall' 'Removing the firewall rules and the autostart task needs admin rights (a UAC prompt follows).'
    exit 0
}

Install-User
if (-not $SkipAdmin) {
    $port, $disc = Get-Ports
    Invoke-Elevated '-AdminOnly' ("Next: inbound firewall rules for TCP $port and UDP $disc (Private networks only) and " +
        "the autostart task at logon. These need admin rights (a UAC prompt follows).")
}
Write-Host ''
Write-Host 'Done. Start now: .venv\Scripts\pythonw.exe -m server.tray  (or log off and on again)' -ForegroundColor Green
