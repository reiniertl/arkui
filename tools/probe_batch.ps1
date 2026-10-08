<#
.SYNOPSIS
  ArkUI coverage probe and batch runner. Windows host. Nothing is pushed.

.DESCRIPTION
  Answers one question per (app, scene): how much of this window does ArkUI
  actually describe? Run it before committing to any instrumentation work.

  NOTHING IS COPIED TO THE DEVICE. On sandboxed builds `hdc file send` and
  `hdc shell` can see different filesystems, so a pushed script reports a
  successful write and is then invisible to the shell. This version runs
  hidumper over `hdc shell` and does all parsing here, which removes that
  failure mode entirely. `scene_probe.sh` remains for running inside a device
  shell directly; it is not needed by this script.

  Windows PowerShell 5.1 compatible.

.EXAMPLE
  .\probe_batch.ps1 -Setup
  .\probe_batch.ps1 -One                       # whatever is in the foreground
  .\probe_batch.ps1 -One -Scene player
  .\probe_batch.ps1 -Windows                   # list windows
  .\probe_batch.ps1 -Plan plan.txt -Out coverage.csv
  .\probe_batch.ps1 -Summary coverage.csv

.NOTES
  If the script is blocked, use probe_batch.cmd beside it, or:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\probe_batch.ps1 -Setup
#>

[CmdletBinding()]
param(
    [string] $Plan,
    [string] $Out = "coverage.csv",
    [string] $Summary,
    [string] $Bundle,
    [string] $Scene = "default",
    [int]    $WindowId = 0,
    [switch] $One,
    [switch] $Windows,
    [switch] $Launch,
    [switch] $NoPrompt,
    [switch] $Setup,
    [switch] $Raw,
    [switch] $ShowCmd,
    [string] $DumpOpt = "inspector",
    [int]    $Settle = 3
)

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
if (-not $Here) { $Here = (Get-Location).Path }

function Die($msg) { Write-Host "error: $msg" -ForegroundColor Red; exit 1 }

# hdc writes ordinary progress to stderr, so stderr alone never means failure.
function Invoke-HdcShell {
    param([string[]] $HdcArgs)
    $out = & hdc shell @HdcArgs 2>&1
    return ($out | ForEach-Object { "$_" })
}

function Invoke-HdcRaw {
    <# PowerShell 5.1 mangles embedded quotes when calling a native .exe, which
       strips the single quotes hidumper needs around its -a argument. Handing
       the whole command line to cmd.exe delivers it byte-for-byte as you would
       type it in a terminal. $CommandLine is everything after "hdc ". #>
    param([string] $CommandLine)
    $full = "hdc $CommandLine"
    if ($ShowCmd) { Write-Host "  > $full" -ForegroundColor DarkGray }
    $out = & cmd.exe /c $full 2>&1
    return ($out | ForEach-Object { "$_" })
}

# ---------------------------------------------------------------- windows

function Get-WindowDump {
    $lines = Invoke-HdcRaw "shell `"hidumper -s WindowManagerService -a '-a'`""
    if (-not $lines -or $lines.Count -lt 2) {
        Die @"
WindowManagerService dump was empty.

  hdc shell id        -> do you have shell privilege?
  hdc list targets    -> is the device still attached?
"@
    }
    return $lines
}

function Get-WindowTable {
    <# Header-driven: column order differs between builds, so find the header
       row and use the position of each name. Observed layout:
         WindowName DisplayId Pid WinId Type Mode Flag ZOrd ...
       Taking "the first integer on the line" picks up DisplayId, which is how
       a window id of 0 or a pid ends up being passed to -w. #>
    param([string[]] $Dump)

    $hdrIdx = -1
    for ($i = 0; $i -lt $Dump.Count; $i++) {
        if ($Dump[$i] -match '(?i)\bWinId\b') { $hdrIdx = $i; break }
    }
    if ($hdrIdx -lt 0) { return @() }

    $cols = ($Dump[$hdrIdx] -split '\s+') | Where-Object { $_ -ne "" }
    $iName = [array]::IndexOf($cols, ($cols | Where-Object { $_ -match '(?i)^WindowName$' } | Select-Object -First 1))
    $iPid  = [array]::IndexOf($cols, ($cols | Where-Object { $_ -match '(?i)^Pid$'        } | Select-Object -First 1))
    $iWin  = [array]::IndexOf($cols, ($cols | Where-Object { $_ -match '(?i)^WinId$'      } | Select-Object -First 1))
    $iFoc  = [array]::IndexOf($cols, ($cols | Where-Object { $_ -match '(?i)^Focus'       } | Select-Object -First 1))

    $rows = @()
    for ($i = $hdrIdx + 1; $i -lt $Dump.Count; $i++) {
        $f = ($Dump[$i] -split '\s+') | Where-Object { $_ -ne "" }
        if ($f.Count -le $iWin) { continue }
        if ($f[$iWin] -notmatch '^\d+$') { continue }
        $rows += [PSCustomObject]@{
            Name  = if ($iName -ge 0 -and $f.Count -gt $iName) { $f[$iName] } else { "?" }
            Pid   = if ($iPid  -ge 0 -and $f.Count -gt $iPid -and $f[$iPid] -match '^\d+$') { [int]$f[$iPid] } else { 0 }
            WinId = [int] $f[$iWin]
            Focus = if ($iFoc -ge 0 -and $f.Count -gt $iFoc) { $f[$iFoc] } else { "" }
            Line  = $Dump[$i]
        }
    }
    return $rows
}

function Resolve-Window {
    param([object[]] $Table, [string] $WantBundle)
    if (-not $Table -or $Table.Count -eq 0) { return $null }
    if ($WantBundle) {
        $hit = $Table | Where-Object { $_.Line -match [regex]::Escape($WantBundle) } | Select-Object -First 1
        if ($hit) { return $hit }
        $short = ($WantBundle -split '\.')[-1]
        $hit = $Table | Where-Object { $_.Name -match [regex]::Escape($short) } | Select-Object -First 1
        if ($hit) { return $hit }
        return $null
    }
    $foc = $Table | Where-Object { $_.Focus -match '(?i)^(1|true|yes)$' } | Select-Object -First 1
    if ($foc) { return $foc }
    return ($Table | Select-Object -Last 1)   # topmost, usually
}

# ---------------------------------------------------------------- elements

function Get-ElementDump {
    param([int] $Id)
    # One argument containing spaces; no shell metacharacters, which is what
    # survives cmd.exe -> hdc.exe -> device shell intact.
    # ArkUI options on this service: element, render, inspector, frontend,
    # navigation. "inspector" is the component tree; "element" returns window
    # properties on at least some builds.
    return Invoke-HdcRaw "shell `"hidumper -s WindowManagerService -a '-w $Id -$DumpOpt'`""
}

function Measure-Tags {
    <# First alphabetic token per line is the component tag in the formats seen
       so far. If TOP TAGS prints nonsense, this is the only function to fix. #>
    param([string[]] $Lines)
    $tally = @{}
    foreach ($l in $Lines) {
        $m = [regex]::Match($l, '[A-Za-z_][A-Za-z_]+')
        if ($m.Success) {
            $t = $m.Value
            if ($tally.ContainsKey($t)) { $tally[$t]++ } else { $tally[$t] = 1 }
        }
    }
    return $tally
}

function Get-Count {
    param([hashtable] $Tally, [string] $Tag)
    if ($Tally.ContainsKey($Tag)) { return [int] $Tally[$Tag] }
    return 0
}

# ---------------------------------------------------------------- engine

function Get-EngineLibs {
    param([int] $ProcId)
    if ($ProcId -le 0) { return "-" }
    try {
        $maps = Invoke-HdcShell @("cat", "/proc/$ProcId/maps")
        $hits = $maps |
            Select-String -Pattern 'lib(flutter|app|unity|il2cpp|cocos|weex|hummer)[a-z0-9_]*\.so' -AllMatches |
            ForEach-Object { $_.Matches } | ForEach-Object { $_.Value } |
            Sort-Object -Unique
        if ($hits) { return ($hits -join " ") }
    } catch { }
    return "-"
}

# ---------------------------------------------------------------- verdict

function Get-Verdict {
    param([int] $Total, [int] $Web, [int] $XComp)
    $opaque = $Web + $XComp
    # Thresholds are starting guesses. Calibrate against one known-native app
    # and one known engine port before trusting the middle cases.
    if ($Total -lt 15 -and $opaque -ge 1) {
        return @("BLIND", "surface-rendered; the tree sees nothing")
    } elseif ($opaque -ge 1 -and $Total -lt 60) {
        return @("THIN", "native shell around a surface; outside attributes only")
    } elseif ($Web -ge 1) {
        return @("MIXED", "needs the ArkWeb record to be complete")
    } elseif ($XComp -ge 1) {
        return @("MIXED", "XComponent present; describable from outside only")
    } elseif ($Total -ge 60) {
        return @("ARKUI", "full coverage")
    }
    return @("SPARSE", "few elements and no opaque node; check the parse with -Raw")
}

# ---------------------------------------------------------------- one probe

function Invoke-Probe {
    param([string] $WantBundle, [string] $SceneLabel, [int] $Id)

    $dump  = Get-WindowDump
    $table = Get-WindowTable -Dump $dump
    $win   = $null
    if ($Id -le 0) {
        $win = Resolve-Window -Table $table -WantBundle $WantBundle
        if (-not $win) {
            Write-Host "  no window matched; run -Windows and pass -WindowId" -ForegroundColor Yellow
            return $null
        }
        $Id = $win.WinId
    } else {
        $win = $table | Where-Object { $_.WinId -eq $Id } | Select-Object -First 1
    }
    $bundleName = $WantBundle
    if (-not $bundleName) {
        if ($win) { $bundleName = $win.Name } else { $bundleName = "unknown" }
    }
    $winPid = 0; if ($win) { $winPid = $win.Pid }

    $elements = Get-ElementDump -Id $Id
    if (-not $elements -or $elements.Count -lt 1) {
        Write-Host @"
  element dump for window $Id was empty.
  Most likely the debug param is unset, or the app was already running when
  you set it. Run -Setup, then force-stop and relaunch the app.
"@ -ForegroundColor Yellow
        return $null
    }

    $tally  = Measure-Tags $elements
    $total  = 0; foreach ($v in $tally.Values) { $total += $v }
    $web    = Get-Count $tally "Web"
    $xcomp  = Get-Count $tally "XComponent"
    $text   = Get-Count $tally "Text"
    $image  = Get-Count $tally "Image"
    $video  = Get-Count $tally "Video"
    $inputs = Get-Count $tally "TextInput"
    $scroll = (Get-Count $tally "List") + (Get-Count $tally "Grid") +
              (Get-Count $tally "Swiper") + (Get-Count $tally "Scroll")
    $engine = Get-EngineLibs -ProcId $winPid

    $v = Get-Verdict -Total $total -Web $web -XComp $xcomp
    $verdict = $v[0]; $note = $v[1]
    if ($engine -ne "-") { $note = "$note; engine libs loaded" }

    Write-Host "bundle    : $bundleName"
    Write-Host "window    : $Id        scene: $SceneLabel"
    Write-Host "elements  : $total"
    Write-Host "opaque    : $($web + $xcomp)   (Web $web, XComponent $xcomp)"
    Write-Host "content   : Text $text, Image $image, Video $video, scrollers $scroll, inputs $inputs"
    Write-Host "engine so : $engine"
    $colour = "Gray"
    switch ($verdict) {
        "ARKUI" { $colour = "Green" }  "MIXED" { $colour = "Yellow" }
        "THIN"  { $colour = "Yellow" } "BLIND" { $colour = "Red" }
        "SPARSE"{ $colour = "Magenta" }
    }
    Write-Host "verdict   : $verdict   ($note)" -ForegroundColor $colour
    Write-Host ""
    Write-Host "TOP TAGS (sanity-check the parse - these should be component names):"
    $tally.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 12 |
        ForEach-Object { Write-Host ("  {0,6}  {1}" -f $_.Value, $_.Key) }

    if ($Raw) {
        $rawFile = Join-Path (Get-Location) ("dump_w{0}_{1}.txt" -f $Id, $SceneLabel)
        $elements | Set-Content -Path $rawFile -Encoding UTF8
        Write-Host ""
        Write-Host "raw dump -> $rawFile"
    }

    return [PSCustomObject]@{
        ts = [int][double]::Parse((Get-Date -UFormat %s))
        bundle = $bundleName; scene = $SceneLabel; window = $Id
        total = $total; opaque = ($web + $xcomp); web = $web; xcomponent = $xcomp
        text = $text; image = $image; video = $video
        scrollers = $scroll; inputs = $inputs
        engine = $engine; verdict = $verdict
    }
}

# ---------------------------------------------------------------- summary

function Show-Summary {
    param([string] $Csv)
    if (-not (Test-Path $Csv)) { Die "no such file: $Csv" }
    $rows = Import-Csv $Csv
    if (-not $rows) { Die "no rows in $Csv" }

    Write-Host ""
    foreach ($r in $rows) {
        $colour = "Gray"
        switch ($r.verdict) {
            "ARKUI" { $colour = "Green" }  "MIXED" { $colour = "Yellow" }
            "THIN"  { $colour = "Yellow" } "BLIND" { $colour = "Red" }
            "SPARSE"{ $colour = "Magenta" }
        }
        Write-Host ("  {0,-32} {1,-14} {2,6} el {3,4} opq  {4}" -f `
                    $r.bundle, $r.scene, $r.total, $r.opaque, $r.verdict) -ForegroundColor $colour
    }
    Write-Host ""
    Write-Host "  verdicts:"
    $rows | Group-Object verdict | Sort-Object Name | ForEach-Object {
        Write-Host ("    {0,-8} {1}" -f $_.Name, $_.Count)
    }
    Write-Host ""
    Write-Host "  ARKUI  full coverage, instrument and go"
    Write-Host "  MIXED  needs the ArkWeb record, or outside-only for XComponent"
    Write-Host "  THIN   native shell around a surface"
    Write-Host "  BLIND  surface-rendered; the tree sees nothing"
    Write-Host ""
}

# ================================================================== main

if ($Summary) { Show-Summary $Summary; exit 0 }

if (-not (Get-Command hdc -ErrorAction SilentlyContinue)) {
    Die "hdc not on PATH. Use the DevEco terminal, or add the SDK toolchains directory."
}
$alive = Invoke-HdcShell @("echo", "ARKPROBE_OK") | Where-Object { $_ -match 'ARKPROBE_OK' }
if (-not $alive) {
    Die @"
no device reachable.
  hdc list targets    -> is a device listed?
  hdc shell id        -> does a shell open at all?
"@
}

if ($Setup) {
    Write-Host "enabling the ArkUI dump path..."
    Invoke-HdcShell @("param", "set", "persist.ace.debug.enabled", "1") | Out-Null
    $v = Invoke-HdcShell @("param", "get", "persist.ace.debug.enabled") |
         Where-Object { $_ -match '\S' } | Select-Object -First 1
    Write-Host "persist.ace.debug.enabled = $v"
    Write-Host ""
    Write-Host "Nothing is copied to the device - this script drives hidumper over" -ForegroundColor Cyan
    Write-Host "hdc shell and parses here." -ForegroundColor Cyan
    Write-Host ""
    Write-Host "IMPORTANT: the dump path is wired at app start, so force-stop and" -ForegroundColor Cyan
    Write-Host "relaunch every app you intend to probe. Apps already running will" -ForegroundColor Cyan
    Write-Host "produce empty dumps." -ForegroundColor Cyan
    Write-Host ""
    exit 0
}

if ($Windows) {
    $d = Get-WindowDump
    $t = Get-WindowTable -Dump $d
    if ($t.Count -gt 0) {
        Write-Host "parsed:" -ForegroundColor Cyan
        $t | Format-Table Name, Pid, WinId, Focus -AutoSize | Out-String | Write-Host
        Write-Host "raw:" -ForegroundColor DarkGray
    }
    $d | ForEach-Object { Write-Host $_ }
    exit 0
}

if ($One) {
    $row = Invoke-Probe -WantBundle $Bundle -SceneLabel $Scene -Id $WindowId
    if ($row -and $Out) {
        if (Test-Path $Out) { $row | Export-Csv -Path $Out -NoTypeInformation -Append }
        else                { $row | Export-Csv -Path $Out -NoTypeInformation }
        Write-Host ""
        Write-Host "row appended -> $Out"
    }
    exit 0
}

if (-not $Plan) { Die "nothing to do. Use -Setup, -One, -Windows, -Plan <file> or -Summary <csv>." }
if (-not (Test-Path $Plan)) { Die "no such plan file: $Plan" }

$rows = @(); $rowNo = 0; $done = 0; $skipped = 0

foreach ($raw in (Get-Content $Plan)) {
    $line = $raw.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { continue }

    $parts   = $line.Split("|")
    $bundleP = $parts[0].Trim()
    $sceneP  = if ($parts.Count -gt 1) { $parts[1].Trim() } else { "" }
    $abilityP= if ($parts.Count -gt 2) { $parts[2].Trim() } else { "" }
    if ($bundleP -eq "") { continue }
    if ($sceneP -eq "")  { $sceneP = "default" }
    $rowNo++

    Write-Host "------------------------------------------------------------"
    Write-Host "[$rowNo] $bundleP    scene: $sceneP" -ForegroundColor Cyan

    if ($Launch -and $abilityP -ne "") {
        Invoke-HdcShell @("aa", "force-stop", $bundleP) | Out-Null
        Write-Host "     launching $abilityP ..."
        Invoke-HdcShell @("aa", "start", "-b", $bundleP, "-a", $abilityP) | Out-Null
        Start-Sleep -Seconds $Settle
    }

    if (-not $NoPrompt) {
        $key = Read-Host "     navigate to '$sceneP', then ENTER   (s=skip, q=quit)"
        if ($key -eq "s" -or $key -eq "S") { Write-Host "     skipped"; $skipped++; continue }
        if ($key -eq "q" -or $key -eq "Q") { Write-Host "     stopping early"; break }
    }

    $row = Invoke-Probe -WantBundle $bundleP -SceneLabel $sceneP -Id 0
    if ($row) { $rows += $row; $done++ }
    Write-Host ""
}

Write-Host "------------------------------------------------------------"
if ($rows.Count -eq 0) { Die "no rows succeeded" }
$rows | Export-Csv -Path $Out -NoTypeInformation
Write-Host "probed $done of $rowNo rows ($skipped skipped) -> $Out" -ForegroundColor Green
Show-Summary $Out
