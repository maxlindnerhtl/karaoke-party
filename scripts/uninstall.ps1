<#
.SYNOPSIS
    Removes Karaoke Party from this Windows PC: undoes what scripts\install.ps1 recorded in data\install-manifest.json.

.DESCRIPTION
    1. stops the server, worker and tray of THIS installation (processes started from its .venv, and their children)
    2. admin, asked first: removes the autostart task and the two firewall rules; switches the network profile back
       only if the installer changed it (asked separately)
    3. asked first, with sizes: deletes .venv, the data folder and the model downloads the worker made
    4. asked first: removes tools the installer added (uv, its Python, ffmpeg, adb); never ones that were already there
    The music folder is never touched. Without a manifest (installs from before manifests), it detects what is there:
    the task and rules by their exact names, .venv, data and the installer's ffmpeg folder; tools and model caches that
    can't be attributed are listed but left alone. The TV side is scripts\tv_uninstall.py.

.PARAMETER Yes
    Answer "yes" to every question (unattended runs, e.g. tests). Admin steps still show the UAC prompt.
.PARAMETER KeepData
    Keep .venv, the data folder (database, processed songs) and the downloaded models.
.PARAMETER KeepTools
    Keep the tools the installer added (uv, its Python, ffmpeg, adb).
.PARAMETER KeepNetworkProfile
    Don't switch the network profile back, even if the installer changed it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1
#>
param(
    [switch]$Yes,
    [switch]$KeepData,
    [switch]$KeepTools,
    [switch]$KeepNetworkProfile,
    [switch]$AdminOnly,
    [string]$RestoreProfile = ''   # elevated run: "<InterfaceIndex>:<Category>" to switch back
)
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$TaskName = 'Karaoke Party server'                                     # exact names, see install.ps1
$Rules = @('KaraokeParty-Server-TCP', 'KaraokeParty-Discovery-UDP')
$ManifestFile = Join-Path $Root 'data\install-manifest.json'
$AdminLog = Join-Path $env:TEMP 'karaoke-party-uninstall-admin.log'

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "    $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }
function Confirm-Step([string]$question) {
    if ($Yes) { Write-Host "$question (y/N) y  [-Yes]"; return $true }
    return ((Read-Host "$question (y/N)") -match '^[yY]')
}
function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# --- admin part (elevated run) -------------------------------------------------------------------------------------

function Remove-AdminItems {
    foreach ($name in $Rules) {
        if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) { Remove-NetFirewallRule -Name $name; Ok "removed firewall rule $name" }
    }
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Ok "removed task '$TaskName'"
    }
    if ($RestoreProfile) {
        $index, $category = $RestoreProfile -split ':', 2
        Set-NetConnectionProfile -InterfaceIndex ([int]$index) -NetworkCategory $category
        Ok "network profile of interface $index set back to $category"
    }
}

if ($AdminOnly) {
    Start-Transcript -Path $AdminLog -Force | Out-Null
    try { Remove-AdminItems } catch { Write-Host "ERROR: $_" -ForegroundColor Red; Stop-Transcript | Out-Null; exit 1 }
    Stop-Transcript | Out-Null
    exit 0
}

# --- what is there ---------------------------------------------------------------------------------------------------

function Read-DotEnv {
    $values = @{}
    $envFile = Join-Path $Root '.env'
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile -Encoding UTF8) {
            if ($line -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$') { $values[$Matches[1]] = $Matches[2].Trim() }
        }
    }
    return $values
}

function Get-FullPath([string]$p) {
    if (-not $p) { return '' }
    if (-not [IO.Path]::IsPathRooted($p)) { $p = Join-Path $Root $p }
    return [IO.Path]::GetFullPath($p).TrimEnd('\')
}

function Test-Inside([string]$path, [string]$folder) {
    if (-not $path -or -not $folder) { return $false }
    $p = (Get-FullPath $path).ToLowerInvariant(); $f = (Get-FullPath $folder).ToLowerInvariant()
    return ($p -eq $f) -or $p.StartsWith($f + '\') -or $f.StartsWith($p + '\')
}

function Get-Size([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return 0 }
    $item = Get-Item -LiteralPath $path -Force
    if (-not $item.PSIsContainer) { return $item.Length }
    $sum = (Get-ChildItem -LiteralPath $path -Recurse -Force -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
    if ($sum) { return [long]$sum } else { return 0 }
}

function Format-Size([long]$n) {
    if ($n -ge 1GB) { return '{0:N1} GB' -f ($n / 1GB) }
    if ($n -ge 1MB) { return '{0:N0} MB' -f ($n / 1MB) }
    return '{0:N0} KB' -f ($n / 1KB)
}

$env_ = Read-DotEnv
$music = Get-FullPath $env_['MUSIC_PATH']
$manifest = $null
if (Test-Path $ManifestFile) { $manifest = Get-Content $ManifestFile -Raw -Encoding UTF8 | ConvertFrom-Json }

$dataDir = Join-Path $Root 'data'
if ($manifest -and $manifest.data_dir) { $dataDir = $manifest.data_dir }
elseif ($env_['DATA_DIR']) { $dataDir = Get-FullPath $env_['DATA_DIR'] }
$dataDirs = @((Join-Path $Root 'data'), $dataDir) | ForEach-Object { Get-FullPath $_ } | Select-Object -Unique

$tools = @()
$caches = @()
$profileChange = $null
if ($manifest) {
    if ($manifest.pc.tools) { $tools = @($manifest.pc.tools | Where-Object { $_.installed_by_us }) }
    if ($manifest.pc.model_caches) { $caches = @($manifest.pc.model_caches) }
    $profileChange = $manifest.pc.network_profile
} else {
    # an install from before manifests: only what certainly belongs to this installation
    $ff = Join-Path $env:LOCALAPPDATA 'karaoke-party'
    if (Test-Path $ff) { $tools = @([pscustomobject]@{ name = 'ffmpeg'; path = $ff; installed_by_us = $true }) }
}

$venvDir = Get-FullPath (Join-Path $Root '.venv')
function Get-OurProcesses {
    $all = @(Get-CimInstance Win32_Process)
    $ours = @($all | Where-Object { ($_.ExecutablePath -and (Test-Inside $_.ExecutablePath $venvDir)) -or
                                    ($_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains($venvDir.ToLowerInvariant())) })
    # plus their children (the venv launcher starts the base interpreter, which starts the worker)
    $ids = [Collections.Generic.HashSet[int]]::new()
    foreach ($p in $ours) { [void]$ids.Add([int]$p.ProcessId) }
    do {
        $added = 0
        foreach ($p in $all) {
            if (-not $ids.Contains([int]$p.ProcessId) -and $ids.Contains([int]$p.ParentProcessId)) { [void]$ids.Add([int]$p.ProcessId); $added++ }
        }
    } while ($added)
    return @($all | Where-Object { $ids.Contains([int]$_.ProcessId) })
}

# --- the plan --------------------------------------------------------------------------------------------------------

Step "Karaoke Party in $Root"
if ($manifest) { Ok "install manifest: $ManifestFile" } else { Warn 'no install manifest (installed before manifests): detecting what is there' }
$procs = Get-OurProcesses
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$rules = @($Rules | Where-Object { Get-NetFirewallRule -Name $_ -ErrorAction SilentlyContinue })
$deletable = @()
foreach ($p in @($venvDir) + $dataDirs + $caches) {
    if ($p -and (Test-Path -LiteralPath $p)) {
        if (Test-Inside $p $music) { Warn "SKIPPED (inside or containing the music folder): $p"; continue }
        $deletable += [pscustomobject]@{ path = $p; size = (Get-Size $p) }
    }
}
$restore = ''
if ($profileChange) {
    $c = Get-NetConnectionProfile -InterfaceIndex $profileChange.interface_index -ErrorAction SilentlyContinue
    if ($c -and $c.NetworkCategory -eq 'Private' -and $profileChange.previous -and $profileChange.previous -ne 'Private') {
        $restore = "$($profileChange.interface_index):$($profileChange.previous)"
    }
}

Write-Host ''
Write-Host 'This will remove:'
Write-Host ("  processes:       " + $(if ($procs) { ($procs | ForEach-Object { "$($_.Name) ($($_.ProcessId))" }) -join ', ' } else { 'none running' }))
Write-Host ("  autostart task:  " + $(if ($task) { "'$TaskName' (admin)" } else { 'none' }))
Write-Host ("  firewall rules:  " + $(if ($rules) { ($rules -join ', ') + ' (admin)' } else { 'none' }))
if ($restore) { Write-Host "  network profile: '$($profileChange.name)' back to $($profileChange.previous) (admin, asked separately)" }
foreach ($d in $deletable) { Write-Host ("  folder:          {0}  ({1}){2}" -f $d.path, (Format-Size $d.size), $(if ($KeepData) { '  KEPT (-KeepData)' } else { '' })) }
foreach ($t in $tools) { Write-Host ("  tool (optional): $($t.name) at $($t.path) (installed by the installer)" + $(if ($KeepTools) { '  KEPT (-KeepTools)' } else { '' })) }
if (-not $manifest) { Write-Host '  model caches:    unknown without a manifest; they may be shared with other apps, so they stay' }
if ($music) { Write-Host "  never touched:   your music folder $music" }
Write-Host ''
if (-not (Confirm-Step 'Go ahead?')) { Warn 'nothing changed'; exit 0 }

# --- 1. processes ----------------------------------------------------------------------------------------------------

Step 'Stopping server, worker and tray'
foreach ($p in $procs) {
    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; Ok "stopped $($p.Name) ($($p.ProcessId))" } catch { }
}
Start-Sleep -Seconds 2

# --- 2. admin: task, rules, network profile ---------------------------------------------------------------------------

if ($KeepNetworkProfile) { $restore = '' }
if ($restore -and -not (Confirm-Step "Switch '$($profileChange.name)' back to $($profileChange.previous)? (the installer changed it to Private)")) { $restore = '' }
if ($task -or $rules -or $restore) {
    Step 'Removing the autostart task and firewall rules (admin)'
    if (Test-Admin) {
        $script:RestoreProfile = $restore
        Remove-AdminItems
    } else {
        $argList = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -AdminOnly"
        if ($restore) { $argList += " -RestoreProfile `"$restore`"" }
        if (Test-Path $AdminLog) { Remove-Item $AdminLog -Force }
        try {
            $p = Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden -ArgumentList $argList
            if (Test-Path $AdminLog) { Get-Content $AdminLog | Where-Object { $_ -match '^\s{4}|ERROR' } | ForEach-Object { Write-Host $_ } }
            if ($p.ExitCode -ne 0) { Warn "the elevated step failed (exit code $($p.ExitCode)); see $AdminLog" }
        } catch {
            # UAC declined or timed out: carry on with the parts that need no admin rights
            Warn 'admin step skipped (the UAC prompt was declined): the task and firewall rules are still there.'
            Warn 'Run this script again to remove them.'
        }
    }
}

# --- 3. folders ------------------------------------------------------------------------------------------------------

if ($deletable -and $KeepData) { Warn 'folders kept (-KeepData)' }
elseif ($deletable) {
    $total = ($deletable | Measure-Object size -Sum).Sum
    Step ("Deleting {0} (.venv, data incl. the database, downloaded models)" -f (Format-Size $total))
    if (Confirm-Step '    Delete these folders? The song database and processed songs are lost (export stats first if you want them).') {
        foreach ($d in $deletable) {
            Remove-Item -LiteralPath $d.path -Recurse -Force -ErrorAction SilentlyContinue
            if (Test-Path -LiteralPath $d.path) { Warn "could not delete $($d.path) (in use?)" } else { Ok "deleted $($d.path)" }
        }
    } else { Warn 'folders kept' }
}

# --- 4. tools --------------------------------------------------------------------------------------------------------

if ($tools -and $KeepTools) { Warn 'tools kept (-KeepTools)' }
elseif ($tools) {
    Step 'Tools the installer added'
    if (Confirm-Step '    Remove them too? (only tools the installer installed; ones you had before stay)') {
        $uvTool = $tools | Where-Object name -eq 'uv' | Select-Object -First 1
        $uvExe = if ($uvTool) { $uvTool.path } else { (Get-Command uv -ErrorAction SilentlyContinue).Source }
        foreach ($t in $tools) {
            switch ($t.name) {
                'python' { if ($uvExe -and (Test-Path $uvExe)) { & $uvExe python uninstall 3.11 2>&1 | Out-Null; Ok 'removed the uv-managed Python 3.11' } }
                'uv' { }   # last, after its Python and cache
                default {
                    if (Test-Inside $t.path $music) { Warn "SKIPPED (music folder): $($t.path)"; continue }
                    Remove-Item -LiteralPath $t.path -Recurse -Force -ErrorAction SilentlyContinue
                    if (Test-Path -LiteralPath $t.path) { Warn "could not delete $($t.path)" } else { Ok "removed $($t.name): $($t.path)" }
                }
            }
        }
        if ($uvTool -and (Test-Path $uvTool.path)) {
            & $uvTool.path cache clean 2>&1 | Out-Null
            $dir = Split-Path $uvTool.path
            foreach ($f in 'uv.exe', 'uvx.exe', 'uvw.exe') { Remove-Item -LiteralPath (Join-Path $dir $f) -Force -ErrorAction SilentlyContinue }
            Ok "removed uv (and its cache) from $dir"
        }
    } else { Warn 'tools kept' }
}

Write-Host ''
Write-Host 'Done. The repository folder itself (code and your .env with the PIN) is still there: delete it when you like.' -ForegroundColor Green
Write-Host 'For the TV: python scripts\tv_uninstall.py --tv <TV IP>   (see UNINSTALL.md)'
