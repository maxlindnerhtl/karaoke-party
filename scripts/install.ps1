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
      6. only if the active network is Public and you agree: switch it to Private (the firewall rules apply there)
    Everything it changes is recorded in data\install-manifest.json, so scripts\uninstall.ps1 can undo exactly that.

.PARAMETER SkipAdmin
    Only the user-level steps.
.PARAMETER AdminOnly
    Only the admin steps (used by the elevated re-run; needs an elevated PowerShell).
.PARAMETER Uninstall
    Runs scripts\uninstall.ps1 (kept for compatibility).
.PARAMETER Yes
    Answer "yes" to every question (unattended runs, e.g. tests). Admin steps still show the UAC prompt.
.PARAMETER NoMl
    Skip the processing stack (torch, demucs, whisperx): for tests only; the server can't process songs then.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#>
param(
    [switch]$SkipAdmin,
    [switch]$AdminOnly,
    [switch]$Uninstall,
    [switch]$Yes,
    [switch]$NoMl,
    [string]$SetPrivate = '',   # elevated run: InterfaceIndex to switch from Public to Private (asked in the user part)
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

function Confirm-Step([string]$question) {
    if ($Yes) { Write-Host "$question (y/N) y  [-Yes]"; return $true }
    return ((Read-Host "$question (y/N)") -match '^[yY]')
}

# install manifest (data\install-manifest.json): what this installer changed, for scripts\uninstall.ps1
function Set-Manifest([string]$cmd, [string]$key, $value) {
    $py = Join-Path $Root '.venv\Scripts\python.exe'
    $json = ConvertTo-Json -InputObject $value -Compress -Depth 5
    # via an environment variable: Windows PowerShell 5.1 strips the double quotes from JSON passed as an argument,
    # and pipes text to native programs as ASCII with a BOM (non-ASCII paths would break)
    $env:KARAOKE_MANIFEST_VALUE = $json
    try {
        & $py (Join-Path $Root 'server\install_manifest.py') $cmd $key '@env' 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Warn "could not record $key in the install manifest" }
    } finally { Remove-Item Env:KARAOKE_MANIFEST_VALUE -ErrorAction SilentlyContinue }
}

function Get-DataDir {
    $d = (Read-DotEnv)['DATA_DIR']
    if (-not $d) { return (Join-Path $Root 'data') }
    if ([IO.Path]::IsPathRooted($d)) { return $d }
    return (Join-Path $Root $d)
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
    $uvOurs = -not (Test-Path $uv)
    if ($uvOurs) {
        # the official installer, pinned; installs to %USERPROFILE%\.local\bin (no admin rights).
        # UV_NO_MODIFY_PATH: leave the user's PATH alone (nothing for the uninstaller to undo); we call uv by its path
        $env:UV_NO_MODIFY_PATH = '1'
        powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/$UvVersion/install.ps1 | iex"
        $uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
        if (-not (Test-Path $uv)) { throw "uv was not installed to $uv" }
    }
    Ok (& $uv --version)

    Step 'Python 3.11 + project environment (torch cu128, demucs, whisperx; the first run downloads several GB)'
    & $uv python find --system 3.11 *> $null   # an existing 3.11 (e.g. from python.org) is used as it is
    $pythonOurs = $LASTEXITCODE -ne 0
    if ($pythonOurs) {
        & $uv python install 3.11
        if ($LASTEXITCODE -ne 0) { throw 'uv python install failed' }
    }
    Push-Location $Root
    try {
        if ($NoMl) { & $uv sync --frozen --no-group ml } else { & $uv sync --frozen }
        if ($LASTEXITCODE -ne 0) { throw 'uv sync failed' }
    } finally { Pop-Location }
    $py = Join-Path $Root '.venv\Scripts\python.exe'
    Set-Manifest set 'pc.os' 'windows'
    Set-Manifest set 'data_dir' (Get-DataDir)
    Set-Manifest add 'pc.tools' @{ name = 'uv'; path = $uv; installed_by_us = $uvOurs }
    $pyFound = (& $uv python find --system 3.11 2>$null | Out-String).Trim()
    Set-Manifest add 'pc.tools' @{ name = 'python'; path = $pyFound; installed_by_us = $pythonOurs; uv_managed = $pythonOurs }
    $cuda = if ($NoMl) { $null } else { & $py -c "import torch; print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')" 2>$null }
    if ($NoMl) { Ok 'GPU check skipped (-NoMl)' } elseif ($cuda) { Ok "torch sees the GPU: $cuda (CUDA)" } else {
        Warn 'torch sees no CUDA GPU: songs will be processed on the CPU. That works, but takes several minutes per song.'
        Warn 'With an NVIDIA GPU: install or update the NVIDIA driver (CUDA 12.8 support, version 570 or newer), then run this again.'
    }

    Step 'ffmpeg with rubberband'
    $e = Read-DotEnv
    $onPath = Get-Command ffmpeg -ErrorAction SilentlyContinue
    $ffmpeg = if ($e['FFMPEG_PATH']) { $e['FFMPEG_PATH'] } elseif ($onPath) { $onPath.Source } else { '' }
    if (Test-Rubberband $ffmpeg) {
        Ok "$ffmpeg has rubberband"
        Set-Manifest add 'pc.tools' @{ name = 'ffmpeg'; path = $ffmpeg; installed_by_us = $false }
    } else {
        if ($ffmpeg) { Warn "$ffmpeg has no rubberband filter; downloading a build that has it" }
        # a private copy, referenced only from .env: PATH is left alone
        $dest = Join-Path $env:LOCALAPPDATA 'karaoke-party'
        $exe = Join-Path $dest "ffmpeg-$FfmpegVersion-essentials_build\bin\ffmpeg.exe"
        Set-Manifest add 'pc.tools' @{ name = 'ffmpeg'; path = $dest; installed_by_us = $true }
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

    Step 'Network profile'
    $script:PublicIndex = ''
    foreach ($c in @(Get-NetConnectionProfile -ErrorAction SilentlyContinue)) {
        if ($c.NetworkCategory -eq 'Public' -and $c.IPv4Connectivity -ne 'Disconnected') {
            Warn "'$($c.Name)' ($($c.InterfaceAlias)) is a Public network: the TV can't reach the server there."
            if (Confirm-Step "    Switch it to Private (admin; scripts\uninstall.ps1 can switch it back)?") {
                $script:PublicIndex = [string]$c.InterfaceIndex
            }
        } else {
            Ok "'$($c.Name)' ($($c.InterfaceAlias)): $($c.NetworkCategory)"
        }
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
    Set-Manifest add 'pc.firewall_rules' $RuleTcp
    Set-Manifest add 'pc.firewall_rules' $RuleUdp

    if ($SetPrivate) {
        $c = Get-NetConnectionProfile -InterfaceIndex ([int]$SetPrivate)
        Set-Manifest set 'pc.network_profile' @{ interface_index = [int]$SetPrivate; interface = $c.InterfaceAlias;
                                                  name = $c.Name; previous = [string]$c.NetworkCategory }
        Set-NetConnectionProfile -InterfaceIndex ([int]$SetPrivate) -NetworkCategory Private
        Ok "'$($c.Name)' is Private now (was $($c.NetworkCategory))"
    }

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
    Set-Manifest set 'pc.task' $TaskName
    Ok "task registered: $pythonw -m server.tray (runs without admin rights)"
}

function Invoke-Elevated([string]$mode, [string]$question) {
    if (Test-Admin) {
        $script:SetPrivate = $script:PublicIndex
        Install-Admin
        return
    }
    Write-Host ''
    Write-Host $question -ForegroundColor Yellow
    if (-not (Confirm-Step 'Continue?')) { Warn 'skipped; run this script again to do it later'; return }
    New-Item -ItemType Directory -Force (Split-Path $AdminLog) | Out-Null
    if (Test-Path $AdminLog) { Remove-Item $AdminLog }
    $argList = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" $mode -User `"$User`""
    if ($script:PublicIndex) { $argList += " -SetPrivate $($script:PublicIndex)" }
    try {
        $p = Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden -ArgumentList $argList
    } catch {
        Warn 'admin step skipped (the UAC prompt was declined); run this script again to do it later'
        return
    }
    if (Test-Path $AdminLog) { Get-Content $AdminLog | ForEach-Object { Write-Host $_ } }
    if ($p.ExitCode -ne 0) { throw "the elevated step failed (exit code $($p.ExitCode)); see $AdminLog" }
}

# --- main -----------------------------------------------------------------------------------------------------

if ($Uninstall) {
    & (Join-Path $PSScriptRoot 'uninstall.ps1')
    exit $LASTEXITCODE
}

if ($AdminOnly) {
    # elevated run: no console to read, so everything also goes to a log the non-elevated run prints
    New-Item -ItemType Directory -Force (Split-Path $AdminLog) | Out-Null
    Start-Transcript -Path $AdminLog -Force | Out-Null
    try {
        Install-Admin
    } catch {
        Write-Host "ERROR: $_" -ForegroundColor Red
        Stop-Transcript | Out-Null
        exit 1
    }
    Stop-Transcript | Out-Null
    exit 0
}

Install-User
if (-not $SkipAdmin) {
    $port, $disc = Get-Ports
    $extra = if ($script:PublicIndex) { ', and switching the Public network to Private' } else { '' }
    Invoke-Elevated '-AdminOnly' ("Next: inbound firewall rules for TCP $port and UDP $disc (Private networks only), " +
        "the autostart task at logon$extra. These need admin rights (a UAC prompt follows).")
}
Write-Host ''
Write-Host 'Done. Start now: .venv\Scripts\pythonw.exe -m server.tray  (or log off and on again)' -ForegroundColor Green
Write-Host 'What this installer changed is listed in data\install-manifest.json; scripts\uninstall.ps1 undoes it.'
