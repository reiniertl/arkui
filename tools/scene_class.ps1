<#
.SYNOPSIS
  Classify the scene on screen from the ArkUI tree. Windows host, nothing pushed.

.DESCRIPTION
  WHAT THIS CAN AND CANNOT SEE

  hidumper gives a SNAPSHOT of the component tree. So:

    available here      structure, roles, text volume, opaque regions, scene
                        class, and - by differencing consecutive snapshots -
                        a coarse churn proxy.

    NOT available here  dirty_measure/layout/render, frames, vsyncs,
                        pending_dirty, gesture events, renderer animations,
                        damage, horizon_ms. Those live inside the running
                        framework and need the collector.

  The churn proxy separates STATIC from CHURNING without per-frame hooks. It
  will UNDER-report a transform-only animation, because a sliding page does
  not change the tree - which is itself the launcher-drag finding.

  Dumps the component tree over hdc, extracts discriminative features, and
  emits a scene CANDIDATE SET with the evidence that produced it.

  It deliberately does not emit a single label. Some scenes are genuinely
  indistinguishable from the tree alone — a fullscreen surface is video, game,
  map or camera — and the honest output is a narrowed set plus the handle that
  resolves it. That is the same contract the collector will have with the
  aggregator, so this is a prototype of the real thing.

  Rule-based on purpose: you can read why it decided, and fix a rule when it
  is wrong.

.EXAMPLE
  .\scene_class.ps1 -WindowId 100
  .\scene_class.ps1 -WindowId 100 -Watch 2
  .\scene_class.ps1 -WindowId 100 -Out scenes.csv
  .\scene_class.ps1 -WindowId 100 -Raw
#>

[CmdletBinding()]
param(
    [int]    $WindowId = 0,
    [string] $Out,
    [int]    $Watch = 0,
    [string] $DumpOpt = "inspector",
    [switch] $Raw,
    [switch] $ListWindows,
    [switch] $ShowCmd,
    [switch] $Explain,
    [string] $Label,
    [switch] $Fit,
    [switch] $Apply,
    [switch] $Deep,
    [switch] $Services,
    [switch] $Window,
    [switch] $Screen,
    [switch] $Classify,
    [switch] $FindRects,
    [switch] $Version,
    [switch] $Separation,
    [switch] $Attrs,
    [switch] $RsProbe,
    [string] $RsFps,
    [string] $Calib = "scene_calib.csv"
)

$ErrorActionPreference = "Stop"
$script:ScreenW     = 0
$script:ScreenH     = 0
$script:ClipAborted = $false
$script:ParseMode   = "?"
$script:SceneContext = @()
$script:PendingWin  = 0
$script:PrevTally   = $null
$script:PrevMono    = 0.0
$script:StableTicks = 0
$script:PrevClass   = ""
$script:Layers      = 0
$script:ImeTop      = 0.0
$script:VpImeClipped = $false


function Invoke-HdcRaw {
    param([string] $CommandLine)
    $full = "hdc $CommandLine"
    if ($ShowCmd) { Write-Host "  > $full" -ForegroundColor DarkGray }
    $out = & cmd.exe /c $full 2>&1
    return ($out | ForEach-Object { "$_" })
}

# Device CLOCK_MONOTONIC seconds. One cheap call per tick, and it is what lets
# this CSV line up with a scope capture or a device-side trace: host timestamps
# share no clock with anything on the phone.
function Get-DeviceMono {
    $l = Invoke-HdcRaw "shell cat /proc/uptime" |
         Where-Object { $_ -match '^\s*[\d.]+' } | Select-Object -First 1
    if ($l) {
        $m = [regex]::Match($l, '^\s*([\d.]+)')
        if ($m.Success) { return [double] $m.Groups[1].Value }
    }
    return 0.0
}

function Get-WindowTable {
    $dump = Invoke-HdcRaw "shell `"hidumper -s WindowManagerService -a '-a'`""
    # Panel size, used later to corroborate the viewport rect parsed out of the
    # component tree. Without this the script cannot tell a window-sized rect
    # from a stray one, and a wrong viewport silently deletes the whole scene.
    $script:ScreenW = 0; $script:ScreenH = 0
    foreach ($l in $dump) {
        $m = [regex]::Match($l, '(\d{3,5})\s*[xX*,]\s*(\d{3,5})')
        if ($m.Success) {
            $w = [int]$m.Groups[1].Value; $h = [int]$m.Groups[2].Value
            # a phone panel, in either orientation, not some other number pair
            if ($w -ge 240 -and $h -ge 240 -and $w -le 8000 -and $h -le 8000 -and
                ($w * $h) -gt ($script:ScreenW * $script:ScreenH)) {
                $script:ScreenW = $w; $script:ScreenH = $h
            }
        }
    }
    $hdr = -1
    for ($i = 0; $i -lt $dump.Count; $i++) {
        if ($dump[$i] -match '(?i)\bWinId\b') { $hdr = $i; break }
    }
    if ($hdr -lt 0) { return @() }
    # Brackets are stripped from header AND rows before splitting, so the
    # bracketed geometry columns ("[OffsetX OffsetY] [Width Height]") line up
    # with their values instead of shifting every index after them.
    $cols = (($dump[$hdr] -replace '[\[\]]', ' ') -split '\s+') | Where-Object { $_ -ne "" }
    function Idx($pat) {
        $h = $cols | Where-Object { $_ -match $pat } | Select-Object -First 1
        if ($h) { return [array]::IndexOf($cols, $h) } else { return -1 }
    }
    $iName = Idx '(?i)^WindowName$'
    $iPid  = Idx '(?i)^Pid$'
    $iWin  = Idx '(?i)^WinId$'
    $iZ    = Idx '(?i)^ZOrd'
    $iVis  = Idx '(?i)^Vis'
    $iFoc  = Idx '(?i)^Focus'
    $iType = Idx '(?i)^Type$'
    $iX    = Idx '(?i)^OffsetX$'
    $iY    = Idx '(?i)^OffsetY$'
    $iW    = Idx '(?i)^(Window)?Width$'
    $iH    = Idx '(?i)^(Window)?Height$'

    $rows = @()
    for ($i = $hdr + 1; $i -lt $dump.Count; $i++) {
        $f = (($dump[$i] -replace '[\[\]]', ' ') -split '\s+') | Where-Object { $_ -ne "" }
        if ($iWin -lt 0 -or $f.Count -le $iWin) { continue }
        if ($f[$iWin] -notmatch '^\d+$') { continue }
        function Fld($ix) { if ($ix -ge 0 -and $f.Count -gt $ix) { $f[$ix] } else { "" } }
        function Num($v) { if ($v -match '^-?\d+$') { [int]$v } else { 0 } }
        $z = Fld $iZ
        $rows += [PSCustomObject]@{
            Name    = if ($iName -ge 0 -and $f.Count -gt $iName) { $f[$iName] } else { "?" }
            Pid     = Fld $iPid
            WinId   = [int] $f[$iWin]
            Type    = Fld $iType
            Visible = Fld $iVis
            Focus   = Fld $iFoc
            ZOrd    = if ($z -match '^-?\d+$') { [int] $z } else { -999999 }
            X       = Num (Fld $iX); Y = Num (Fld $iY)
            W       = Num (Fld $iW); H = Num (Fld $iH)
        }
    }
    return $rows
}

# Window names that are system chrome rather than an app's content. Extend this
# list if the guess keeps landing on the wrong thing.
# Windows that are system chrome rather than app content.
#
# SCB* are SceneBoard windows. Only the OVERLAYS are listed here - gesture hot
# zones, status bars, panels, blur decoration. SCBDesktop and the other
# launcher surfaces are deliberately NOT excluded: the launcher is a scene you
# want to profile, and it legitimately sits at a high ZOrd.
#
# Tune without editing this file: -Exclude '<regex>' is appended.
$script:SystemWindowPat =
    '(?i)scbgesture|gestureback|gesturenav|' +
    'scbwallpaper|scbstatusbar|scbnavigation|scbvolume|scbdropdown|' +
    'scbscreenlock|scbbanner|scbnotification|' +
    'blurview|backgroundblur|' +
    'statusbar|navigationbar|navbar|wallpaper|systemui|keyguard|lock|dock|' +
    'recent|pointer|cursor|keyboard|softkeyboard|inputmethod|ime|toast|volume|' +
    'notification|dropdown|controlpanel|launcherdock|divider|drag'

# ---------------------------------------------------------------- scene scope
#
# WHAT IS "THE SCENE".
#
# Four different things get called the same word, and the classifier is only
# meaningful once you say which one it is labelling:
#
#   window          a WindowManager object: rect, ZOrder, type, pid.
#   viewport        that window's rect clipped to the display.
#   visible region  the viewport minus whatever sits on top of it. A window
#                   can be "visible" and entirely covered by a dialog.
#   rendered set    the nodes inside the visible region that are not clipped
#                   by a scroller, hidden, or transparent.
#
# The definition used here:
#
#   THE SCENE IS THE TOPMOST NON-OVERLAY WINDOW THAT COVERS A MATERIAL SHARE
#   OF THE DISPLAY. Everything above it is CONTEXT, not a separate scene.
#
# That matters because a phone screen is nearly always several windows - a
# launcher under a status bar under a gesture strip - and calling the topmost
# one "the scene" is how the classifier ended up describing a 40-pixel gesture
# hot zone. The thing a user would point at is the big one underneath.
#
# Picking by ZOrder alone cannot express this. Picking by area alone picks the
# wallpaper. It needs both, plus occlusion: if something above genuinely
# covers the host, THAT is the scene and the host is behind it.
# The window table on this build prints no geometry columns, but every
# per-window dump opens with a header that does:
#
#   WindowRect: [ 0, 0, 1316, 2832 ]
#   Offset: [ 0, 0 ]
#
# One extra call per window buys back share, occlusion and the panel size -
# which is the whole of the scene-scoping logic. Cached for the session,
# because window rects change on rotation and split, not on every tick.
$script:RectCache = @{}
# The window being examined, in display coordinates. Get-Features uses it as
# the viewport, so it must be set before any tree is parsed.
$script:WinRect = $null

function Set-WinRect {
    param($Row)
    if ($Row -and $Row.W -gt 1 -and $Row.H -gt 1) {
        $script:WinRect = [PSCustomObject]@{ X = [int]$Row.X; Y = [int]$Row.Y; W = [int]$Row.W; H = [int]$Row.H }
    } else { $script:WinRect = $null }
}

function Repair-WindowRects {
    param([object[]] $Table, [int] $Max = 12)

    if (-not $Table -or $Table.Count -eq 0) { return }
    if (@($Table | Where-Object { $_.W -gt 0 }).Count -gt 0) { return }   # table had them

    $n = 0
    foreach ($w in $Table) {
        if ($n -ge $Max) { break }
        $n++
        $id = [int]$w.WinId
        if (-not $script:RectCache.ContainsKey($id)) {
            $hdr = Invoke-HdcRaw "shell `"hidumper -s WindowManagerService -a '-w $id -element'`""
            $r = $null
            foreach ($l in $hdr) {
                $m = [regex]::Match($l, '(?i)WindowRect\s*:\s*\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\]')
                if ($m.Success) {
                    # [x, y, width, height] - the header gives an origin and a
                    # size, not two corners.
                    $r = @{ X=[int][double]$m.Groups[1].Value; Y=[int][double]$m.Groups[2].Value
                            W=[int][double]$m.Groups[3].Value; H=[int][double]$m.Groups[4].Value }
                    break
                }
            }
            $script:RectCache[$id] = $r
        }
        $r = $script:RectCache[$id]
        if ($r) {
            $w.X = $r.X; $w.Y = $r.Y; $w.W = $r.W; $w.H = $r.H
            if ($r.W -gt $script:ScreenW) { $script:ScreenW = $r.W; $script:ScreenH = $r.H }
        }
    }
}

# Area is not occlusion. Share is rect area over panel area, so two windows
# that both fill the display both report 100% and the shares sum to 200%. What
# actually reaches the panel at any point is the TOP window there, so "how much
# of the display is this window" can only be answered after the windows above
# it have taken their share.
#
# Exact, by coordinate compression: every rect edge becomes a grid line, and
# each cell belongs to the highest-z window covering it. Ten windows is about
# four hundred cells, so exactness costs nothing and region algebra is not
# needed.
#
# It assumes every window is OPAQUE, because nothing in the WMS table says
# otherwise, and a transparent full-screen panel will therefore claim
# everything beneath it. That is not a detail: an IME host window is exactly
# that shape, and it is why the name pattern still has to carry the overlay
# decision rather than the geometry carrying it alone.
function Set-Occlusion {
    param([object[]] $Wins, [double] $PanelW, [double] $PanelH)

    if (-not $Wins -or $Wins.Count -eq 0 -or $PanelW -le 0 -or $PanelH -le 0) { return 0 }
    $rs = @()
    foreach ($w in $Wins) {
        Add-Member -InputObject $w -NotePropertyName Unocc -NotePropertyValue -1.0 -Force
        if ($w.W -le 0 -or $w.H -le 0) { continue }
        $l = [math]::Max(0.0, [double]$w.X)
        $t = [math]::Max(0.0, [double]$w.Y)
        $r = [math]::Min($PanelW, [double]$w.X + [double]$w.W)
        $b = [math]::Min($PanelH, [double]$w.Y + [double]$w.H)
        if ($r -le $l -or $b -le $t) { continue }
        $w.Unocc = 0.0
        $rs += [PSCustomObject]@{ Win = $w; Z = [int]$w.ZOrd; L = $l; T = $t; R = $r; B = $b }
    }
    if ($rs.Count -eq 0) { return 0 }

    $xs = @(@(0.0, $PanelW) + @($rs | ForEach-Object { $_.L; $_.R }) | Sort-Object -Unique)
    $ys = @(@(0.0, $PanelH) + @($rs | ForEach-Object { $_.T; $_.B }) | Sort-Object -Unique)
    $area = $PanelW * $PanelH
    $layers = @{}
    for ($i = 0; $i -lt $xs.Count - 1; $i++) {
        $cx = ([double]$xs[$i] + [double]$xs[$i+1]) / 2.0
        for ($j = 0; $j -lt $ys.Count - 1; $j++) {
            $cy = ([double]$ys[$j] + [double]$ys[$j+1]) / 2.0
            $top = $null
            foreach ($rr in $rs) {
                if ($cx -ge $rr.L -and $cx -lt $rr.R -and $cy -ge $rr.T -and $cy -lt $rr.B) {
                    if ($null -eq $top -or $rr.Z -gt $top.Z) { $top = $rr }
                }
            }
            if ($top) {
                $cell = ([double]$xs[$i+1] - [double]$xs[$i]) * ([double]$ys[$j+1] - [double]$ys[$j])
                $top.Win.Unocc += $cell / $area
                $layers[[string]$top.Win.WinId] = $true
            }
        }
    }
    return $layers.Count
}

function Resolve-Scene {
    param([object[]] $Table, [switch] $Quiet)

    if (-not $Table -or $Table.Count -eq 0) { return $null }
    $ctx = @()

    $displayArea = 0.0
    if ($script:ScreenW -gt 0) { $displayArea = [double]$script:ScreenW * $script:ScreenH }
    if ($displayArea -le 0) {
        foreach ($w in $Table) { $a = [double]$w.W * $w.H; if ($a -gt $displayArea) { $displayArea = $a } }
    }
    $haveRects = ($displayArea -gt 0)

    $visible = @($Table | Where-Object { $_.Visible -eq "" -or $_.Visible -match '(?i)^(1|true|yes)$' })
    if ($visible.Count -eq 0) { $visible = $Table }

    foreach ($w in $visible) {
        $area = [double]$w.W * $w.H
        $share = if ($haveRects -and $area -gt 0) { $area / $displayArea } else { -1.0 }
        Add-Member -InputObject $w -NotePropertyName Share -NotePropertyValue $share -Force
        # An overlay is system chrome by name, or - when we have rects - simply
        # too small to be a scene whatever it is called.
        $byName = ($w.Name -match $script:SystemWindowPat)
        $bySize = ($share -ge 0 -and $share -lt 0.15)
        Add-Member -InputObject $w -NotePropertyName IsOverlay -NotePropertyValue ($byName -or $bySize) -Force
    }

    # How much of the panel each window actually reaches, after the windows
    # above it have taken theirs.
    $pw = [double]$script:ScreenW; $ph = [double]$script:ScreenH
    if ($pw -le 0 -or $ph -le 0) {
        $big = $visible | Sort-Object { [double]$_.W * [double]$_.H } -Descending | Select-Object -First 1
        if ($big) { $pw = [double]$big.W; $ph = [double]$big.H }
    }
    $script:Layers = Set-Occlusion -Wins $visible -PanelW $pw -PanelH $ph
    # Raw shares summing well past the panel means full-screen rects stacked on
    # each other, which in practice means transparency the table does not
    # declare. Worth saying out loud rather than quietly dividing it up.
    $sumShare = 0.0
    foreach ($w in $visible) { if ($w.Share -gt 0) { $sumShare += $w.Share } }
    if ($sumShare -ge 1.6) { $ctx += "OVERLAPPING_FULLSCREEN" }

    # Candidates: not chrome, and big enough to be the thing on screen.
    $content = @($visible | Where-Object { -not $_.IsOverlay -and ($_.Share -lt 0 -or $_.Share -ge 0.35) })
    if ($content.Count -eq 0) { $content = @($visible | Where-Object { -not $_.IsOverlay }) ; $ctx += "NO_CONTENT_WINDOW" }
    if ($content.Count -eq 0) { $content = $visible }

    $ranked = @($content | Sort-Object ZOrd -Descending)
    $host_ = $ranked[0]

    # Occlusion. A window above that covers most of the host IS the scene.
    if ($haveRects) {
        foreach ($w in $visible) {
            if ($w.WinId -eq $host_.WinId -or $w.ZOrd -le $host_.ZOrd) { continue }
            if ($w.IsOverlay) { continue }
            $ox = [math]::Max(0, [math]::Min($w.X + $w.W, $host_.X + $host_.W) - [math]::Max($w.X, $host_.X))
            $oy = [math]::Max(0, [math]::Min($w.Y + $w.H, $host_.Y + $host_.H) - [math]::Max($w.Y, $host_.Y))
            $hostArea = [double]$host_.W * $host_.H
            if ($hostArea -gt 0 -and (($ox * $oy) / $hostArea) -ge 0.8) {
                $ctx += "PROMOTED_OVER_$($host_.WinId)"
                $host_ = $w
            }
        }
    }

    # What sits above the scene is context, and each of these changes the cost
    # shape without changing what the scene IS.
    foreach ($w in $visible) {
        if ($w.ZOrd -le $host_.ZOrd) { continue }
        if ($w.Name -match '(?i)input_?method|softkeyboard|keyboard|\bime\b') {
            $ctx += "IME_UP"
            # Where the keyboard starts is the bottom of the usable screen. A
            # composer sits just above it, which on a 2832px panel with the
            # IME up is about 57% down - well short of EditBotMin, so the
            # composer rule stood down and CHAT scored nothing on a chat.
            # edit_pos has to be normalised against what the user can see.
            if ($w.H -gt 0 -and $w.Y -gt 0) {
                if ($script:ImeTop -le 0 -or $w.Y -lt $script:ImeTop) { $script:ImeTop = [double]$w.Y }
            }
            continue
        }
        if ($w.Name -match '(?i)notification|dropdown|shade|controlpanel')   { $ctx += "SHADE_ABOVE"; continue }
        if ($w.Name -match '(?i)volume|toast|banner')                        { $ctx += "TRANSIENT_ABOVE"; continue }
        if (-not $w.IsOverlay) { $ctx += "WINDOW_ABOVE" }
    }
    if (@($content | Where-Object { $_.Share -ge 0.2 }).Count -ge 2) { $ctx += "MULTI_WINDOW" }
    if (-not $haveRects) { $ctx += "NO_WINDOW_RECTS" }

    $script:SceneContext = @($ctx | Select-Object -Unique)

    if (-not $Quiet) {
        $sh = if ($host_.Share -ge 0) { "{0}% of the display" -f [int]($host_.Share * 100) } else { "size unknown" }
        Write-Host ("scene: window {0} ({1})  z{2}  {3}" -f $host_.WinId, $host_.Name, $host_.ZOrd, $sh) -ForegroundColor DarkCyan
        if ($script:SceneContext.Count -gt 0) {
            Write-Host ("       context: {0}" -f ($script:SceneContext -join ", ")) -ForegroundColor DarkGray
        }
        $others = @($ranked | Select-Object -Skip 1 -First 2)
        if ($others.Count -gt 0) {
            Write-Host ("       under it: {0}" -f (($others | ForEach-Object { "$($_.WinId)/$($_.Name)/z$($_.ZOrd)" }) -join ", ")) -ForegroundColor DarkGray
        }
    }
    return $host_
}


$script:KnownDumpOpts = @('inspector','element','render','frontend','navigation','uitest')

function Get-Tree {
    param([int] $Id)
    # A mistyped source used to reach hidumper as an unknown flag, which
    # answers with the window header and nothing else: 29 lines, no rects, and
    # an output that looks like a real degraded result instead of a typo.
    if ($script:KnownDumpOpts -notcontains $DumpOpt) {
        Write-Host ("error: -DumpOpt '{0}' is not a source. Use one of: {1}" -f `
                    $DumpOpt, ($script:KnownDumpOpts -join ", ")) -ForegroundColor Red
        exit 1
    }
    # uitest is not a hidumper view; it is a different tool with a different
    # shape, so it is fetched, flattened and scoped before anything else sees it.
    if ($DumpOpt -eq "uitest") { return Get-UiTestTree -Id $Id }
    return Invoke-HdcRaw "shell `"hidumper -s WindowManagerService -a '-w $Id -$DumpOpt'`""
}

# ---------------------------------------------------------------- features

# Rect parsing. The dump format moves between builds, so three shapes are
# tried and the first hit wins. If none hits anywhere in the dump, geometry is
# UNAVAILABLE, which is a different thing from flat: every geometry-dependent
# column is then reported as absent and the rules that need it stand down
# instead of guessing.
function Get-Rect {
    param([string] $L)
    $r = Get-RectRaw $L
    # A degenerate rect means "not measured", NOT "off screen". Auto-sized
    # nodes print 0x0 in several dump formats, and treating those as scrolled
    # out deleted most of the tree and collapsed every scene into the
    # fallback class. Unknown geometry must leave the node in the scene.
    if ($r -and (($r.R - $r.L) -le 0.5 -or ($r.B - $r.T) -le 0.5)) { return $null }
    return $r
}

function Get-RectRaw {
    param([string] $L)
    # "bounds":"[left,top][right,bottom]" - uitest. Checked first and
    # anchored on the key, because origBounds sits beside it with the
    # pre-transform rect and taking that one silently shifts every node.
    $m = [regex]::Match($L, '(?i)"bounds"\s*:\s*"\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\]\s*\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\]')
    if ($m.Success) {
        return @{ L=[double]$m.Groups[1].Value; T=[double]$m.Groups[2].Value
                  R=[double]$m.Groups[3].Value; B=[double]$m.Groups[4].Value }
    }
    # "[left,top],[right,bottom]" - the ArkUI inspector $rect form
    $m = [regex]::Match($L, '\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\]\s*,\s*\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\]')
    if ($m.Success) {
        return @{ L=[double]$m.Groups[1].Value; T=[double]$m.Groups[2].Value
                  R=[double]$m.Groups[3].Value; B=[double]$m.Groups[4].Value }
    }
    # "(x, y) - [w x h]"
    $m = [regex]::Match($L, '\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)\s*-?\s*\[\s*(-?[\d.]+)\s*x\s*(-?[\d.]+)\s*\]')
    if ($m.Success) {
        $x=[double]$m.Groups[1].Value; $y=[double]$m.Groups[2].Value
        $w=[double]$m.Groups[3].Value; $h=[double]$m.Groups[4].Value
        return @{ L=$x; T=$y; R=($x+$w); B=($y+$h) }
    }
    # separate keys
    $mw = [regex]::Match($L, '(?i)\bwidth\s*[:=]\s*"?(-?[\d.]+)')
    $mh = [regex]::Match($L, '(?i)\bheight\s*[:=]\s*"?(-?[\d.]+)')
    if ($mw.Success -and $mh.Success) {
        $mx = [regex]::Match($L, '(?i)(?:^|[^a-z])(?:x|left|offsetX)\s*[:=]\s*"?(-?[\d.]+)')
        $my = [regex]::Match($L, '(?i)(?:^|[^a-z])(?:y|top|offsetY)\s*[:=]\s*"?(-?[\d.]+)')
        $x = if ($mx.Success) { [double]$mx.Groups[1].Value } else { 0.0 }
        $y = if ($my.Success) { [double]$my.Groups[1].Value } else { 0.0 }
        return @{ L=$x; T=$y; R=($x+[double]$mw.Groups[1].Value); B=($y+[double]$mh.Groups[1].Value) }
    }
    return $null
}

# The dump comes in one of two layouts: one node per line, or a pretty-printed
# block where $type, $rect and the attributes are on separate lines. Deciding
# which ONCE, up front, is what makes node assembly reliable - a per-line
# fallback applied to a block dump turns every attribute into a fake node.
function Get-Nodes {
    param([string[]] $Lines)

    # Only the inspector's own key counts: "$type" or a quoted "type". A bare
    # `Type:` line from the window property header must NOT switch the parser
    # into block mode, because in block mode every line without that key is
    # folded into the previous node - which turns a whole tree into one node.
    $typePat = '(?:^|[\s,{])"?\$type"?\s*[:=]\s*"?([A-Za-z_][A-Za-z0-9_]*)|(?:^|[\s,{])"type"\s*:\s*"?([A-Za-z_][A-Za-z0-9_]*)'
    $hits = 0
    foreach ($l in $Lines) { if ($l -match $typePat) { $hits++; if ($hits -ge 3) { break } } }
    $typed = ($hits -ge 3)

    $nodes = Build-Nodes -Lines $Lines -Typed $typed -TypePat $typePat
    # Self-correcting: if block mode produced almost nothing from a dump with
    # real content, the key guess was wrong. Fall back rather than hand the
    # classifier an empty tree.
    if ($typed -and $nodes.Count -lt 5) {
        $body = 0; foreach ($l in $Lines) { if (-not [string]::IsNullOrWhiteSpace($l)) { $body++ } }
        if ($body -gt 20) {
            $nodes = Build-Nodes -Lines $Lines -Typed $false -TypePat $typePat
            $script:ParseMode = "per-line (block mode yielded $($nodes.Count))"
            return ,$nodes
        }
    }
    $script:ParseMode = if ($typed) { "block" } else { "per-line" }
    return ,$nodes
}

function Build-Nodes {
    param([string[]] $Lines, [bool] $Typed, [string] $TypePat)

    $nodes = New-Object System.Collections.ArrayList
    $cur = $null
    foreach ($l in $Lines) {
        if ([string]::IsNullOrWhiteSpace($l)) { continue }
        $start = $false; $tag = $null
        if ($Typed) {
            $m = [regex]::Match($l, $TypePat)
            if ($m.Success) {
                $start = $true
                $tag = if ($m.Groups[1].Success) { $m.Groups[1].Value } else { $m.Groups[2].Value }
            }
        } else {
            $m = [regex]::Match($l, '[A-Za-z_][A-Za-z_]+')
            if ($m.Success) { $start = $true; $tag = $m.Value }
        }
        if ($start) {
            if ($cur) { [void]$nodes.Add($cur) }
            $indent = $l.Length - $l.TrimStart().Length
            $cur = @{ Tag=$tag; Indent=$indent; Text=$l; Rect=$null; Leaf=$true }
        } elseif ($cur) {
            $cur.Text += " " + $l
        } else { continue }
        if ($cur -and -not $cur.Rect) {
            $r = Get-Rect $l
            if ($r) { $cur.Rect = $r }
        }
    }
    if ($cur) { [void]$nodes.Add($cur) }

    # Leaf detection by indentation. Needed because the ROOT always fills the
    # window: measure the largest node and every screen looks like fullscreen
    # video. The largest LEAF is the content.
    for ($i = 0; $i -lt $nodes.Count - 1; $i++) {
        if ($nodes[$i+1].Indent -gt $nodes[$i].Indent) { $nodes[$i].Leaf = $false }
    }
    return ,$nodes
}


# ---- opaque regions: what is probably behind the surface ----------------
#
# We cannot look inside an XComponent, and never will - it is app-owned
# content. But the DECLARATION is ours, and it carries more than it looks:
#
#   id            the developer's own name for it. Free text, so a guess, but
#                 in practice people call a video surface "videoPlayer".
#   libraryname   the native .so registered to drive it. "ijkplayer" and
#                 "nativerender" say very different things.
#   type          SURFACE composites on its own; TEXTURE is read back into the
#                 UI tree, which costs an extra pass. A cost difference, not
#                 just a taxonomy.
#   enableSecure  DRM. Not a guess - protected content is video.
#   accessibility the one string written to be human-readable.
#
# None of this is authoritative. The buffer queue knows the truth: usage flags
# literally say video decoder or camera, and the format says YUV or RGBA. This
# is what ArkUI can offer BEFORE that join, and a prior to weigh it against.
function Get-OpaqueHint {
    param($Nodes, $F)

    $name=""; $lib=""; $xtype=""; $a11y=""; $secure=0; $hdr=0; $aspect=0.0
    foreach ($n in $Nodes) {
        $t = $n.Text
        if (-not $name) {
            $m = [regex]::Match($t, '(?i)\b(?:inspectorKey|componentId|\bid|\bkey)\s*[:=]\s*"?([A-Za-z0-9_.\-]{2,48})')
            if ($m.Success -and $m.Groups[1].Value -notmatch '^\d+$') { $name = $m.Groups[1].Value }
        }
        if (-not $lib) {
            $m = [regex]::Match($t, '(?i)librar(?:y)?[_ ]?name\s*[:=]\s*"?([A-Za-z0-9_.\-]{1,48})')
            if ($m.Success) { $lib = $m.Groups[1].Value }
        }
        if (-not $xtype) {
            $m = [regex]::Match($t, '(?i)(?:xcomponent)?type\s*[:=]\s*"?(surface|texture|component|node)\b')
            if ($m.Success) { $xtype = $m.Groups[1].Value.ToUpper() }
        }
        if (-not $a11y) {
            $m = [regex]::Match($t, '(?i)accessibility(?:Text|Description|Label)\s*[:=]\s*"?([^"|,]{2,60})')
            if ($m.Success) { $a11y = $m.Groups[1].Value.Trim() }
        }
        if ($t -match '(?i)enablesecure\s*[:=]\s*"?true') { $secure = 1 }
        if ($t -match '(?i)hdrbrightness|isHdr\s*[:=]\s*"?true') { $hdr = 1 }
        if ($n.VisW -gt 0 -and $n.VisH -gt 0) {
            $a = $n.VisW / $n.VisH
            if ($a -gt $aspect) { $aspect = $a }
        }
    }

    $hay  = ("$name $lib $a11y").ToLower()
    $hint = "UNKNOWN"; $conf = 0; $why = @(); $flags = @()

    # Keyword tables. Deliberately generous - a wrong guess at 40 confidence
    # costs the aggregator nothing, a missing guess costs it a prior.
    $tables = @(
        @{ H="VIDEO";     Pat='video|player|media|movie|film|vod|live|stream|playback|mp4|avplayer|ijk|exo|ffmpeg|vlc|danmaku' },
        @{ H="CAMERA";    Pat='camera|cam[^a-z]|preview|viewfinder|capture|lens|shutter|scan|qrcode|barcode' },
        @{ H="GAME";      Pat='game|unity|unreal|cocos|egl|gles|opengl|vulkan|engine|render3d|scene3d|sprite' },
        @{ H="MAP";       Pat='map|navi|gis|tile|amap|baidumap|route|geo' },
        @{ H="CHART";     Pat='chart|graph|plot|gauge|candle|kline' },
        @{ H="AR";        Pat='\bar\b|\bxr\b|slam|arkit|arengine' },
        @{ H="AUDIO_VIS"; Pat='visualizer|spectrum|waveform|equalizer' }
    )
    foreach ($tb in $tables) {
        if ($hay -match $tb.Pat) {
            $hint = $tb.H; $conf = 55
            $why += "name/library matched $($tb.H.ToLower())"
            $flags += if ($lib -match $tb.Pat) { "LIBRARY_MATCH" } else { "NAME_MATCH" }
            if ($a11y -match $tb.Pat) { $flags += "A11Y_MATCH"; $conf += 10 }
            break
        }
    }

    # Facts outrank substrings.
    if ($secure) {
        $flags += "SECURE"
        if ($hint -eq "UNKNOWN" -or $hint -eq "VIDEO") { $hint = "VIDEO"; $conf = [math]::Max($conf, 85) }
        $why += "DRM-protected surface"
    }
    if ($hdr) { $flags += "HDR"; if ($hint -eq "UNKNOWN") { $hint = "VIDEO"; $conf = 70 }; $why += "HDR" }

    # Structure around it, when the declaration says nothing.
    if ($hint -eq "UNKNOWN") {
        if ($F.Slider -ge 1 -and $F.Button -ge 2) {
            $hint = "VIDEO"; $conf = 45; $flags += "SIBLING_MATCH"; $why += "seekbar and transport beside the surface"
        } elseif ($F.Button -ge 4 -and $F.Slider -eq 0) {
            $hint = "CAMERA"; $conf = 35; $flags += "SIBLING_MATCH"; $why += "button cluster, no seekbar"
        }
    }
    if ($aspect -ge 1.6 -and $aspect -le 1.85) {
        $flags += "ASPECT_MATCH"
        if ($hint -eq "VIDEO") { $conf = [math]::Min(95, $conf + 10) }
        elseif ($hint -eq "UNKNOWN") { $hint = "VIDEO"; $conf = 30; $why += "16:9 surface" }
    }
    # TEXTURE is read back into the UI tree: an extra pass, worth saying.
    if ($xtype -eq "TEXTURE") { $why += "TEXTURE type - composited through the UI tree, not independently" }

    return [PSCustomObject]@{
        Name = $name; Lib = $lib; XType = $xtype; A11y = $a11y
        Secure = $secure; Hdr = $hdr
        Aspect = [math]::Round($aspect, 2)
        Hint = $hint; Conf = $conf
        Flags = ($flags | Select-Object -Unique) -join "|"
        Why = ($why -join "; ")
    }
}

# Every node the walk STOPS at. One definition, used by the feature counter
# and by the metadata dump alike, because "opaque" has to mean the same thing
# in both places: a node whose contents we do not own and cannot descend into.
# Web is on this list even though ArkWeb is patchable platform code - from the
# component tree's point of view it is still a hole, and the inside of it is a
# separate producer's job.
$script:OpaquePat = '^(XComponent|Web|Video|SurfaceView|EmbeddedComponent|UIExtensionComponent|Plugin|RichEditor_Surface)$'
# The subset of the above that is another ArkUI instance in another process
# rather than a raw buffer queue. Different hole, different way to close it.
$script:EmbedPat  = '^(EmbeddedComponent|UIExtensionComponent|Plugin)$'

$script:SurfaceKnownKeys = @(
    'type','xcomponenttype','id','inspectorkey','componentid','key','libraryname','library_name',
    'surfaceid','surface_id','uniqueid','nodeid','webid','nwebid','rect','framerect','x','y','left',
    'top','right','bottom','width','height','size','offsetx','offsety','enablesecure','hdrbrightness',
    'ishdr','enableanalyzer','renderfit','accessibilitytext','accessibilitydescription',
    'accessibilitylabel','accessibilitylevel','accessibilitygroup','opacity','visibility','enabled',
    'clip','active','backgroundcolor','foregroundcolor','zindex','compid','debugline','isroot',
    'src','rendermode','layoutmode','incognito','javascriptaccess','darkmode','zoomaccess',
    'mediaplaygestureaccess','nestedscroll','mixedmode','cachemode','blocknetwork','autoplay',
    'controls','loop','muted','objectfit','currentprogressrate','poster','bundlename','abilityname',
    'pluginname','want'
)

# Ordered attribute list for the printer: label, value, and a short note on why
# the value matters. Empty values are dropped rather than printed as blanks -
# an absent attribute and an attribute set to nothing are different facts, and
# only the first one is worth a line.
function Add-Attr {
    param($List, [string] $K, [string] $V, [string] $N)
    if ($V) { [void]$List.Add([PSCustomObject]@{ K=$K; V=$V; N=$N }) }
}

# Pull one attribute out of a node's accumulated text.
function Get-Attr {
    param([string] $T, [string] $Pat)
    $m = [regex]::Match($T, $Pat)
    if ($m.Success) { return $m.Groups[1].Value.Trim().Trim('"') }
    return ""
}

# A URL is the most identifying thing a Web node carries and the one piece of
# this that is somebody's browsing history. The host answers every question we
# have - which engine instance, which site, is it the same page as last
# interval - and the path answers none of them, so the path is printed for the
# person at the terminal and never written to the CSV.
function Split-Url {
    param([string] $U)
    if (-not $U) { return [PSCustomObject]@{ Host=""; Short="" } }
    $m = [regex]::Match($U, '(?i)^([a-z][a-z0-9+.\-]*):(?://)?([^/?#\s]*)([^\s?#]*)')
    if (-not $m.Success) { return [PSCustomObject]@{ Host=""; Short=($U.Substring(0, [math]::Min(48, $U.Length))) } }
    $scheme = $m.Groups[1].Value.ToLower()
    $hostName = $m.Groups[2].Value
    $path = $m.Groups[3].Value
    if ($path.Length -gt 24) { $path = $path.Substring(0, 24) + "..." }
    $short = if ($hostName) { "$scheme`://$hostName$path" } else { "$scheme`:$path" }
    return [PSCustomObject]@{ Host=$hostName; Short=$short }
}

# ---- metadata for every opaque node -------------------------------------
#
# One record per node, never one per window. Two opaque regions on a screen
# are two independent cost sources - a 900-permille player and a 120-permille
# preview submit at different rates and neither is described by their average.
#
# The attributes split three ways:
#   common      geometry, visibility, and what the node is wrapped in. These
#               mean the same thing whatever is behind the hole.
#   per kind    an XComponent has a library and a surface type; a Web has a
#               render mode and a URL. Different questions, different answers.
#   unclaimed   every other key=value the node carries. The named sets above
#               encode what we EXPECTED a declaration to hold; builds differ,
#               and the only way to learn what this one emits is to print the
#               keys nobody asked for.
function Get-SurfaceRecords {
    param($Nodes, [int] $VpW = 0, [int] $VpH = 0)

    # The tree is not the screen. It carries pages beneath the top of the
    # route stack, recycled list items and hidden tab contents, and every
    # opaque node in them is in this walk. Which of them you are actually
    # looking at is a geometry question, so without rects the honest answer
    # is UNKNOWN - not "on screen", which is the mistake that collapsed the
    # classifier twice already.
    $clipW = if ($VpW -gt 0) { $VpW } else { $script:ScreenW }
    $clipH = if ($VpH -gt 0) { $VpH } else { $script:ScreenH }

    $recs = New-Object System.Collections.ArrayList
    for ($i = 0; $i -lt $Nodes.Count; $i++) {
        $n = $Nodes[$i]
        if ($n.Tag -notmatch $script:OpaquePat) { continue }
        $t = $n.Text

        # ---- common ------------------------------------------------------
        $id  = Get-Attr $t '(?i)\b(?:inspectorKey|componentId|id|key)\s*[:=]\s*"?([A-Za-z0-9_.\-]{2,48})'
        if ($id -match '^\d+$') { $id = "" }
        $opa = Get-Attr $t '(?i)\bopacity\s*[:=]\s*"?([\d.]+)'
        $vsb = Get-Attr $t '(?i)\bvisibility\s*[:=]\s*"?([A-Za-z]+)'

        # The join key. Several spellings across builds and across kinds; all
        # numeric. Without one, nothing downstream can be matched to this
        # node - not a buffer queue, not an RS layer, not a power interval.
        $join = Get-Attr $t '(?i)\b(?:surface[_ ]?id|nweb[_ ]?id|web[_ ]?id|uniqueId|nodeId)\s*[:=]\s*"?([0-9]{3,20})'
        $joinKind = ""
        if ($join) {
            if ($t -match '(?i)surface[_ ]?id')      { $joinKind = "surfaceId" }
            elseif ($t -match '(?i)n?web[_ ]?id')    { $joinKind = "webId" }
            else                                      { $joinKind = "nodeId" }
        }

        # Geometry. The share is of the PANEL, not of the parent, because that
        # is the number the descriptor carries and the one that scales with
        # composition cost.
        $w = 0; $h = 0; $permille = -1; $aspect = 0.0
        if ($n.Rect) {
            $w = [int]($n.Rect.R - $n.Rect.L); $h = [int]($n.Rect.B - $n.Rect.T)
            if ($h -gt 0) { $aspect = [math]::Round([double]$w / [double]$h, 3) }
            if ($script:ScreenW -gt 0 -and $script:ScreenH -gt 0) {
                $permille = [int](1000.0 * ($w * $h) / ([double]$script:ScreenW * $script:ScreenH))
            }
        }

        # What it is wrapped in. A Slider and two Buttons alongside is player
        # chrome; the same node alone under a Column is immersive.
        # True siblings: everything under the same parent at the same depth,
        # however far away. A fixed +/-8 window missed the seekbar in a real
        # player - the record read "alone in its parent, no chrome" on the
        # same screen where the classifier scored "surface + seekbar". One of
        # them had to be wrong and it was this one.
        $parent = ""; $pIdx = -1
        for ($j = $i - 1; $j -ge 0; $j--) {
            if ($Nodes[$j].Indent -lt $n.Indent) { $parent = $Nodes[$j].Tag; $pIdx = $j; break }
        }
        $sibs = @(); $near = @()
        if ($pIdx -ge 0) {
            $pInd = $Nodes[$pIdx].Indent
            for ($j = $pIdx + 1; $j -lt $Nodes.Count; $j++) {
                if ($Nodes[$j].Indent -le $pInd) { break }
                if ($j -eq $i) { continue }
                if ($Nodes[$j].Indent -eq $n.Indent) { $sibs += $Nodes[$j].Tag }
                # Controls anywhere in the parent's subtree, not only at the
                # surface's own depth - chrome is usually wrapped a level down.
                if ($Nodes[$j].Tag -match '^(Slider|Progress|Button|Toggle|Checkbox|Text|Image|SymbolGlyph)$') {
                    $near += $Nodes[$j].Tag
                }
            }
        }

        # ---- per kind ----------------------------------------------------
        # Attrs is an ordered list of label/value/note so the printer does not
        # need to know anything about kinds, and a new kind is one block here.
        $attrs = New-Object System.Collections.ArrayList
        $flags = @()
        $kind = switch -Regex ($n.Tag) {
            '^XComponent$'                             { "XCOMPONENT" }
            '^Web$'                                    { "WEB" }
            '^Video$'                                  { "VIDEO" }
            '^SurfaceView$'                            { "SURFACE_VIEW" }
            '^(EmbeddedComponent|UIExtensionComponent)$' { "EMBEDDED" }
            '^Plugin$'                                 { "PLUGIN" }
            default                                    { "OPAQUE" }
        }
        $srcHost = ""

        if ($kind -eq "XCOMPONENT" -or $kind -eq "SURFACE_VIEW") {
            $xt = (Get-Attr $t '(?i)(?:xcomponent)?type\s*[:=]\s*"?(surface|texture|component|node)\b').ToUpper()
            Add-Attr $attrs "libraryname" (Get-Attr $t '(?i)librar(?:y)?[_ ]?name\s*[:=]\s*"?([A-Za-z0-9_.\-]{1,48})') ""
            Add-Attr $attrs "type"        $xt $(switch ($xt) {
                                    "TEXTURE"   { "read back through the UI tree - an extra pass" }
                                    "SURFACE"   { "own composition layer" }
                                    "COMPONENT" { "drawn by the app on the UI thread" }
                                    default     { "" } })
            Add-Attr $attrs "renderFit"   (Get-Attr $t '(?i)renderFit\s*[:=]\s*"?([A-Za-z_]+)') "how the buffer is fitted - implies scaling"
            if ($t -match '(?i)enablesecure\s*[:=]\s*"?true')  { $flags += "SECURE (DRM)" }
            if ($t -match '(?i)hdrbrightness\s*[:=]\s*"?[\d.]+|\bisHdr\s*[:=]\s*"?true') { $flags += "HDR" }
            if ($t -match '(?i)enableanalyzer\s*[:=]\s*"?true') { $flags += "ANALYZER (AI runs over the frames)" }
        }
        elseif ($kind -eq "WEB") {
            $u = Split-Url (Get-Attr $t '(?i)\bsrc\s*[:=]\s*"?([^"\s,}]{4,300})')
            $srcHost = $u.Host
            Add-Attr $attrs "src"        $u.Short "host is kept; the path is not written to the CSV"
            $rm = (Get-Attr $t '(?i)renderMode\s*[:=]\s*"?([A-Za-z_]+)').ToUpper()
            Add-Attr $attrs "renderMode" $rm $(if ($rm -match 'SYNC') { "drawn into the UI tree - same cost shape as a TEXTURE XComponent" }
                                 elseif ($rm -match 'ASYNC') { "own composition layer" } else { "" })
            $lm = (Get-Attr $t '(?i)layoutMode\s*[:=]\s*"?([A-Za-z_]+)').ToUpper()
            Add-Attr $attrs "layoutMode" $lm $(if ($lm -match 'FIT_CONTENT') { "measured by content height - web layout drives ArkUI measure" } else { "" })
            Add-Attr $attrs "cacheMode"  (Get-Attr $t '(?i)cacheMode\s*[:=]\s*"?([A-Za-z_]+)') ""
            Add-Attr $attrs "mixedMode"  (Get-Attr $t '(?i)mixedMode\s*[:=]\s*"?([A-Za-z_]+)') ""
            Add-Attr $attrs "nestedScroll" (Get-Attr $t '(?i)nestedScroll[A-Za-z]*\s*[:=]\s*"?([A-Za-z_]+)') "web scroll chained to an ArkUI scroller"
            if ($t -match '(?i)incognito\s*[:=]\s*"?true')              { $flags += "INCOGNITO" }
            if ($t -match '(?i)javaScriptAccess\s*[:=]\s*"?false')      { $flags += "JS OFF" }
            if ($t -match '(?i)darkMode\s*[:=]\s*"?(On|Auto)')          { $flags += "DARK MODE" }
            if ($t -match '(?i)mediaPlayGestureAccess\s*[:=]\s*"?true') { $flags += "MEDIA GESTURE" }
            if ($t -match '(?i)blockNetwork\s*[:=]\s*"?true')           { $flags += "NETWORK BLOCKED" }
        }
        elseif ($kind -eq "VIDEO") {
            $u = Split-Url (Get-Attr $t '(?i)\bsrc\s*[:=]\s*"?([^"\s,}]{4,300})')
            $srcHost = $u.Host
            Add-Attr $attrs "src"       $u.Short ""
            Add-Attr $attrs "objectFit" (Get-Attr $t '(?i)objectFit\s*[:=]\s*"?([A-Za-z_]+)') ""
            Add-Attr $attrs "rate"      (Get-Attr $t '(?i)currentProgressRate\s*[:=]\s*"?([\d.]+)') "playback rate - off 1.0 changes the decode load"
            if ($t -match '(?i)autoPlay\s*[:=]\s*"?true') { $flags += "AUTOPLAY" }
            if ($t -match '(?i)\bloop\s*[:=]\s*"?true')   { $flags += "LOOP" }
            if ($t -match '(?i)\bmuted\s*[:=]\s*"?true')  { $flags += "MUTED" }
            if ($t -match '(?i)\bcontrols\s*[:=]\s*"?true') { $flags += "BUILT-IN CONTROLS" }
        }
        elseif ($kind -eq "EMBEDDED" -or $kind -eq "PLUGIN") {
            # The provider IS the identity here - the content belongs to
            # another process entirely, so there is nothing else to read.
            Add-Attr $attrs "bundleName"  (Get-Attr $t '(?i)bundleName\s*[:=]\s*"?([A-Za-z0-9_.\-]{2,80})') "the process that owns what is inside"
            Add-Attr $attrs "abilityName" (Get-Attr $t '(?i)abilityName\s*[:=]\s*"?([A-Za-z0-9_.\-]{2,80})') ""
            Add-Attr $attrs "pluginName"  (Get-Attr $t '(?i)pluginName\s*[:=]\s*"?([A-Za-z0-9_.\-]{2,80})') ""
        }

        Add-Attr $attrs "a11y" (Get-Attr $t '(?i)accessibility(?:Text|Description|Label)\s*[:=]\s*"?([^"|,]{2,60})') "the one string written to be read by a human"

        # ---- unclaimed ---------------------------------------------------
        $extra = @()
        foreach ($m in [regex]::Matches($t, '([A-Za-z_][A-Za-z0-9_]{2,31})\s*[:=]\s*"?([^",}\s][^",}]{0,38})')) {
            $k = $m.Groups[1].Value
            if ($script:SurfaceKnownKeys -contains $k.ToLower()) { continue }
            $extra += ("{0}={1}" -f $k, $m.Groups[2].Value.Trim())
        }
        $extra = @($extra | Select-Object -Unique | Select-Object -First 24)

        $onScreen = -1
        if ($n.Rect -and $clipW -gt 0 -and $clipH -gt 0) {
            $ox = [math]::Min($n.Rect.R, [double]$clipW) - [math]::Max($n.Rect.L, 0.0)
            $oy = [math]::Min($n.Rect.B, [double]$clipH) - [math]::Max($n.Rect.T, 0.0)
            $onScreen = if ($ox -gt 0.5 -and $oy -gt 0.5) { 1 } else { 0 }
        }

        [void]$recs.Add([PSCustomObject]@{
            Idx = $recs.Count; Tag = $n.Tag; Kind = $kind; OnScreen = $onScreen
            JoinId = $join; JoinKind = $joinKind
            Id = $id; SrcHost = $srcHost
            Opacity = $opa; Vis = $vsb
            W = $w; H = $h; Aspect = $aspect; Permille = $permille
            Parent = $parent; Siblings = (($sibs | Select-Object -Unique) -join ",")
            Near = ((($near | Select-Object -Unique) | Select-Object -First 10) -join ",")
            Attrs = @($attrs); Flags = $flags
            Extra = $extra
        })
    }
    return ,$recs
}

# ------------------------------------------------------------------- scopes
#
# Two views of the same window, same layout, one difference:
#
#   -Window   everything the window CONTAINS. The whole tree, including the
#             pages under the top of the route stack and the list items that
#             are laid out but scrolled past.
#   -Screen   only what is INSIDE THE VIEWPORT right now.
#
# They are different questions with different answers, and the gap between
# them is the point. A window containing a media player is a media player
# window for as long as it is open. Scroll the player out of view and the
# screen is a list - same window, same tree, different pixels, and almost
# certainly a different power draw, because the decoder is still running but
# nothing it produces reaches the display.
#
# Nothing here classifies. It reports.

function Get-TallyCount {
    param($T, [string[]] $Keys)
    $n = 0
    if (-not $T) { return 0 }
    foreach ($k in $Keys) { if ($T.ContainsKey($k)) { $n += $T[$k] } }
    return $n
}

# What a node sits next to, said plainly. Siblings are evidence about an
# opaque region precisely because the surface itself tells us nothing: a
# seekbar beside a hole is a statement about what is behind the hole, made by
# the only part of the screen we can read.
function Get-SiblingNote {
    param($Rec, $F)
    # Same-level siblings first, then anything under the same parent. A player
    # often wraps its controls one level down, and "no chrome" said about a
    # surface that has a seekbar two lines away is worse than saying nothing.
    $s = $Rec.Siblings
    $where = "beside it"
    if (-not $s) { $s = $Rec.Near; $where = "under the same parent" }
    if (-not $s) {
        # Nothing adjacent. If the WINDOW has transport controls somewhere
        # else, say so: the classifier scores that co-occurrence and the two
        # lines reading differently is confusing unless the difference is
        # named. Chrome far from the surface is also weaker evidence than
        # chrome beside it, which is worth seeing.
        if ($F -and ($F.Slider + $F.Progress) -ge 1) {
            return "no chrome beside it, but the window has a slider/progress elsewhere"
        }
        return "nothing around it in the tree - no chrome to read"
    }
    $notes = @()
    $btn = ([regex]::Matches($s, '(?i)\bButton\b')).Count
    if ($s -match '(?i)\bSlider\b' -and $btn -ge 2) { $notes += "seekbar + transport buttons: a player with controls" }
    elseif ($s -match '(?i)\bSlider\b')             { $notes += "a slider alongside: seekable or adjustable" }
    elseif ($btn -ge 4)                             { $notes += "$btn buttons, no seekbar: a capture or tool bar" }
    if ($s -match '(?i)\bProgress\b')               { $notes += "progress indicator alongside" }
    if ($Rec.Parent -match '(?i)List|Grid|Waterflow|Swiper') { $notes += "inside a $($Rec.Parent) cell: one item among many, not the screen" }
    if ($s -match '(?i)\bText\b' -and $notes.Count -eq 0)    { $notes += "text alongside: captioned or labelled" }
    if ($notes.Count -eq 0) { return "nothing recognisable $where" }
    return (($notes -join "; ") + " ($where)")
}

function Show-Scope {
    param($F, [int] $Id, [string] $WinName, [string] $ProcId, [string] $Mode)

    $screen = ($Mode -eq "screen")
    $tally  = if ($screen) { $F.Tally } else { $F.TallyAll }
    $total  = if ($screen) { $F.Total } else { $F.Nodes }

    Write-Host ""
    if ($screen) {
        Write-Host ("SCREEN  of window {0}  ({1})" -f $Id, $WinName) -ForegroundColor Cyan
        Write-Host  "  scope: only the nodes inside the viewport right now" -ForegroundColor DarkGray
    } else {
        Write-Host ("WINDOW  {0}  ({1})  pid {2}" -f $Id, $WinName, $ProcId) -ForegroundColor Cyan
        Write-Host  "  scope: everything the window contains, on screen or not" -ForegroundColor DarkGray
    }

    # ---- can we even tell the two apart on this build? -------------------
    $clipOn = [bool]$F.VpTrusted
    Write-Host ""
    if ($clipOn) {
        Write-Host ("  viewport   {0}x{1}   panel {2}x{3}   clip ON{4}" -f $F.VpW, $F.VpH, $F.ScreenW, $F.ScreenH,
                    $(if ($script:VpImeClipped) { "   (bottom cut at the keyboard)" } else { "" }))
        Write-Host ("  on screen  {0} of {1} nodes   ({2} scrolled out, {3} hidden)" -f `
                    $F.Total, $F.Nodes, $F.OffScreen, $F.Hidden)
    } else {
        $why = if (-not $F.GeoOK) { "this dump carries no node rects" }
               elseif ($F.ClipAborted) { "the viewport was rejected - it would have deleted most of the tree" }
               else { "no rect matched the panel size, so the viewport is unconfirmed" }
        Write-Host ("  geometry   UNAVAILABLE - {0}" -f $why) -ForegroundColor DarkYellow
        if ($screen) {
            Write-Host  "  SCREEN CANNOT BE SEPARATED FROM WINDOW on this build." -ForegroundColor Yellow
            Write-Host  "  What follows is the whole window again. Scrolling will not change it." -ForegroundColor Yellow
            Write-Host  "  Next thing to try:  -DumpOpt render   then   -RsProbe" -ForegroundColor Yellow
        }
    }

    # uitest sees the whole display, so say which window's subtree this is
    # and what else was on screen. A silent filter is as misleading as none.
    if ($DumpOpt -eq "uitest") {
        $wins = if ($script:UiTestWindows) { $script:UiTestWindows -join "  " } else { "none reported" }
        Write-Host ("  source     uitest, display-wide: {0}" -f $wins) -ForegroundColor DarkGray
        switch ($script:UiTestScope) {
            "WIN" { Write-Host ("             scoped to window {0}" -f $Id) -ForegroundColor DarkGray }
            "ALL" { Write-Host ("  WARNING    no node claims window {0}; counts cover every window above" -f $Id) -ForegroundColor Yellow }
            "UNLABELLED" {
                Write-Host "             this build's uitest does not label nodes by window, so this is" -ForegroundColor DarkGray
                Write-Host "             the composed screen, not one window. Fine when one app fills it." -ForegroundColor DarkGray
            }
        }
    }

    # uitest walks the ACCESSIBILITY tree, which contains only what is on
    # screen. "0 scrolled out" from this source is a property of the source,
    # not a statement about the screen - and window scope cannot come from it
    # at all, because the nodes that are off screen were never in the dump.
    #
    # The inspector has the opposite shape: the whole window, no geometry, no
    # text. So the one question window scope was for - does this window still
    # contain a player when you cannot see one - is answered from there. Not
    # the counts, which are on a different scale entirely and would be
    # meaningless side by side; just what opaque nodes exist.
    if (-not $screen -and $DumpOpt -eq "uitest") {
        Write-Host ""
        Write-Host "  NOTE       uitest carries only on-screen nodes, so WINDOW scope is not" -ForegroundColor DarkYellow
        Write-Host "             available from this source. What follows is the screen." -ForegroundColor DarkYellow
        $ins = @(Invoke-HdcRaw "shell `"hidumper -s WindowManagerService -a '-w $Id -inspector'`"")
        if ($ins.Count -gt 5) {
            $cnt = @{}
            foreach ($tag in @('XComponent','Web','Video','SurfaceView')) {
                $cnt[$tag] = @($ins | Where-Object { $_ -match ("\b" + $tag + "\b") }).Count
            }
            $tot = 0; foreach ($v in $cnt.Values) { $tot += $v }
            $sum = (($cnt.GetEnumerator() | Where-Object { $_.Value -gt 0 } |
                     ForEach-Object { "{0} {1}" -f $_.Value, $_.Key }) -join ", ")
            Write-Host ("  whole tree {0} lines in the inspector dump; opaque nodes: {1}" -f `
                        $ins.Count, $(if ($tot -gt 0) { $sum } else { "none" })) -ForegroundColor DarkCyan
            $onNow = $F.XComponent + $F.Web + $F.Video
            if ($tot -gt 0 -and $onNow -eq 0) {
                Write-Host "             the window still holds a surface that is not on screen." -ForegroundColor DarkCyan
            }
        }
    }

    # What attribute keys this source actually emits. Asked once, it settles
    # a whole class of question: a feature reading zero everywhere is either
    # absent from the screen or absent from the vocabulary, and those need
    # opposite responses. Switches were the case in point - a type in the
    # inspector, an attribute in uitest.
    if (-not $screen -and $DumpOpt -eq "uitest") {
        $keys = @{}
        foreach ($n in $F.NodeList) {
            foreach ($m in [regex]::Matches($n.Text, '"([A-Za-z_][A-Za-z0-9_]{1,31})"\s*:')) {
                $keys[$m.Groups[1].Value] = $true
            }
        }
        if ($keys.Count -gt 0) {
            Write-Host ""
            Write-Host ("  attributes {0}" -f ((@($keys.Keys) | Sort-Object) -join " ")) -ForegroundColor DarkGray
        }
    }

    # ---- what the window itself declares ---------------------------------
    # Only in window scope: these are properties of the window, not of what
    # happens to be scrolled into view.
    if (-not $screen) {
        $props = Get-WindowProps -Id $Id
        $hit = @()
        foreach ($k in $props.Keys) { if ($k -match $script:WindowPropsOfInterest) { $hit += ("{0}={1}" -f $k, $props[$k]) } }
        Write-Host ""
        if ($hit.Count -gt 0) { Write-Host ("  declares   {0}" -f (($hit | Select-Object -First 12) -join "   ")) }
        else { Write-Host "  declares   nothing recognised in the window property dump" -ForegroundColor DarkGray }
    }

    # ---- composition ------------------------------------------------------
    $text   = Get-TallyCount $tally @('Text','Span','RichText')
    $image  = Get-TallyCount $tally @('Image')
    $icon   = Get-TallyCount $tally @('SymbolGlyph','Symbol','ImageSpan','ImageAnimator')
    $button = Get-TallyCount $tally @('Button')
    $edit   = Get-TallyCount $tally @('TextInput','TextArea','Search','RichEditor')
    $list   = Get-TallyCount $tally @('List','ListItem','ListItemGroup','LazyForEach')
    $grid   = Get-TallyCount $tally @('Grid','GridItem','WaterFlow','FlowItem')
    $swiper = Get-TallyCount $tally @('Swiper','Tabs','TabContent')
    $scroll = Get-TallyCount $tally @('Scroll','Scroller','Refresh')
    $slider = Get-TallyCount $tally @('Slider','Progress')
    $xc     = Get-TallyCount $tally @('XComponent')
    $web    = Get-TallyCount $tally @('Web')
    $vid    = Get-TallyCount $tally @('Video','SurfaceView')

    Write-Host ""
    Write-Host ("  nodes      {0}" -f $total)
    Write-Host ("  content    text {0}   image {1}   icon {2}   button {3}   editable {4}   toggle {5}" -f `
                $text, $image, $icon, $button, $edit, $F.Toggle)
    Write-Host ("  containers list {0}   grid {1}   swiper/tabs {2}   scroll {3}" -f $list, $grid, $swiper, $scroll)
    Write-Host ("  media      XComponent {0}   Web {1}   Video {2}   slider/progress {3}" -f $xc, $web, $vid, $slider)

    # The record stating its own completeness. Printed always, not only when
    # it is bad, so that a low number is visible as reassurance rather than
    # an absence that has to be assumed.
    $cov = $F.CoverOpaque
    $covCol = if ($cov -ge $P.OpaqueCover) { "Yellow" } elseif ($cov -ge 200) { "DarkYellow" } else { "DarkGray" }
    Write-Host ("  coverage   {0} permille of the viewport is behind a surface (Web {1}, XComponent {2})" -f `
                $cov, $F.CoverWeb, $F.CoverXc) -ForegroundColor $covCol
    if ($cov -ge $P.OpaqueCover) {
        Write-Host "  WARNING    most of what is on screen is NOT in this tree. What you can see" -ForegroundColor Yellow
        Write-Host "             may be drawn inside the surface - an icon list, a feed, a map -" -ForegroundColor Yellow
        Write-Host "             and ArkUI cannot tell. The counts below describe the chrome." -ForegroundColor Yellow
        $need = @()
        if ($F.CoverWeb -ge 1) { $need += "the ArkWeb producer" }
        if ($F.CoverXc  -ge 1) { $need += "render_service (-RsProbe) for the surface" }
        if ($need.Count -gt 0) {
            Write-Host ("             this window needs {0} to be described." -f ($need -join " and ")) -ForegroundColor DarkCyan
        }
    }

    # ---- opaque nodes -----------------------------------------------------
    $vw = if ($clipOn) { $F.VpW } else { 0 }
    $vh = if ($clipOn) { $F.VpH } else { 0 }
    $all = Get-SurfaceRecords -Nodes $F.NodeList -VpW $vw -VpH $vh
    $recs = if ($screen -and $clipOn) { @($all | Where-Object { $_.OnScreen -eq 1 }) } else { @($all) }

    Write-Host ""
    if ($all.Count -eq 0) {
        Write-Host "  OPAQUE     none in this window" -ForegroundColor DarkGray
    } elseif ($recs.Count -eq 0) {
        Write-Host ("  OPAQUE     none on screen - {0} exist in the window but are scrolled out" -f $all.Count) -ForegroundColor DarkYellow
    } else {
        Write-Host ("  OPAQUE     {0}" -f $recs.Count) -ForegroundColor Green
    }

    foreach ($r in $recs) {
        $state = switch ($r.OnScreen) { 1 { "on screen" } 0 { "OFF SCREEN" } default { "on screen?" } }
        Write-Host ""
        Write-Host ("    [{0}] {1}  ({2})  {3}" -f $r.Idx, $r.Tag, $r.Kind, $state) -ForegroundColor Green
        if ($r.JoinId) { Write-Host ("         {0,-12} {1}" -f $r.JoinKind, $r.JoinId) }
        else           { Write-Host ("         {0,-12} not in this dump" -f "joinId") -ForegroundColor DarkGray }
        if ($r.Id)     { Write-Host ("         {0,-12} {1}" -f "id", $r.Id) }
        foreach ($a in $r.Attrs) { Write-Host ("         {0,-12} {1}" -f $a.K, $a.V) }
        if ($r.W -gt 0) {
            $sh = if ($r.Permille -ge 0) { "{0} permille of panel" -f $r.Permille } else { "" }
            Write-Host ("         {0,-12} {1}x{2}  aspect {3}  {4}" -f "rect", $r.W, $r.H, $r.Aspect, $sh)
        } else {
            Write-Host ("         {0,-12} not in this dump" -f "rect") -ForegroundColor DarkGray
        }
        if ($r.Flags.Count -gt 0) { Write-Host ("         {0,-12} {1}" -f "flags", ($r.Flags -join "  ")) }
        if ($r.Parent)   { Write-Host ("         {0,-12} {1}" -f "parent", $r.Parent) }
        if ($r.Siblings) { Write-Host ("         {0,-12} {1}" -f "siblings", $r.Siblings) }
        if ($r.Near)     { Write-Host ("         {0,-12} {1}" -f "under parent", $r.Near) }
        Write-Host     ("         {0,-12} {1}" -f "reads as", (Get-SiblingNote -Rec $r -F $F)) -ForegroundColor DarkCyan
        if ($r.Extra.Count -gt 0) {
            Write-Host ("         {0,-12} {1}" -f "other keys", (($r.Extra | Select-Object -First 8) -join "  ")) -ForegroundColor DarkGray
        }
    }

    # ---- rows, when asked for --------------------------------------------
    if ($Out -and $recs.Count -gt 0) {
        $sdir = Split-Path -Parent $Out
        $sf   = if ($sdir) { Join-Path $sdir "scene_surfaces.csv" } else { "scene_surfaces.csv" }
        $had  = Test-Path $sf
        $recs | Select-Object @{n='scope';e={$Mode}}, @{n='win';e={$Id}}, @{n='window_name';e={$WinName}},
                              Idx, Tag, Kind, OnScreen, JoinKind, JoinId, Id, SrcHost,
                              W, H, Aspect, Permille, Vis, Opacity, Parent, Siblings,
                              @{n='flags';e={ $_.Flags -join '|' }},
                              @{n='attrs';e={ ($_.Attrs | ForEach-Object { "$($_.K)=$($_.V)" }) -join '|' }},
                              @{n='extra';e={ $_.Extra -join '|' }} |
            Export-Csv -Path $sf -NoTypeInformation -Append:$had
        Write-Host ""
        Write-Host ("  rows -> {0}" -f $sf) -ForegroundColor DarkGray
    }

    # ---- the comparison, stated ------------------------------------------
    Write-Host ""
    if ($clipOn) {
        $hiddenOpaque = @($all | Where-Object { $_.OnScreen -eq 0 }).Count
        if ($screen -and $hiddenOpaque -gt 0) {
            Write-Host ("  {0} opaque node(s) are in this window but not on screen. The decoder may" -f $hiddenOpaque) -ForegroundColor DarkCyan
            Write-Host  "  still be running while nothing it produces reaches the display." -ForegroundColor DarkCyan
        }
        Write-Host ("  run the other scope to compare:  scene_class.cmd {0} -WindowId {1}" -f `
                    $(if ($screen) { "-Window" } else { "-Screen" }), $Id) -ForegroundColor DarkGray
    }
}

# -------------------------------------------------------------------- uitest
#
# -FindRects settled it: on this build every hidumper view returns the same
# window header, only the inspector returns a body, and that body is a bare
# tree of component names. No rects, no text, no attributes anywhere. The
# layout results exist - the device is drawing them - but nothing in the
# window manager's dumps prints them.
#
# uitest does. It is the UI-test harness that ships with the system, and its
# dumpLayout walks the live accessibility tree and writes one JSON object per
# node with bounds, type, text, id, description and the host window id. That
# is everything the inspector withholds, from a channel that exists for
# exactly this purpose.
#
# The cost is honesty about what it is: a test harness, heavier than a dump,
# and it reads the accessibility projection of the tree rather than the tree
# itself - decorative nodes may be absent, and a node marked
# accessibility-hidden will not appear. For building a labelled dictionary
# that is a good trade. For the collector it is irrelevant: in-process the
# real tree is right there.

# Split uitest's JSON into one indented line per node. Indentation is the
# depth of the children arrays, which restores the parent/sibling structure
# the rest of the script reads.
#
# String literals are skipped while scanning, because "bounds":"[0,0][1,2]"
# contains brackets and counting those as nesting would scramble every depth
# below it.
function ConvertFrom-UiTestLayout {
    param([string] $Json)

    $out = New-Object System.Collections.ArrayList
    $len = $Json.Length
    $depth = 0
    $i = 0
    while ($i -lt $len) {
        $c = $Json[$i]

        # Every string is read as a unit, key or value alike. Scanning past
        # them is not optional: "bounds":"[0,0][1,2]" carries brackets, and
        # counting those as nesting scrambles the depth of everything below.
        if ($c -eq '"') {
            $st = $i + 1
            $j = $st
            while ($j -lt $len) {
                if ($Json[$j] -eq '\') { $j += 2; continue }
                if ($Json[$j] -eq '"')  { break }
                $j++
            }
            $key = $Json.Substring($st, $j - $st)
            $i = $j + 1

            if ($key -eq 'attributes') {
                $k = $i
                while ($k -lt $len -and ($Json[$k] -eq ':' -or $Json[$k] -eq ' ' -or $Json[$k] -eq "`t" -or $Json[$k] -eq "`r" -or $Json[$k] -eq "`n")) { $k++ }
                if ($k -lt $len -and $Json[$k] -eq '{') {
                    $d = 0; $e = $k
                    while ($e -lt $len) {
                        $ch = $Json[$e]
                        if ($ch -eq '"') {
                            $e++
                            while ($e -lt $len) {
                                if ($Json[$e] -eq '\') { $e += 2; continue }
                                if ($Json[$e] -eq '"')  { break }
                                $e++
                            }
                        } elseif ($ch -eq '{') { $d++ }
                        elseif ($ch -eq '}') { $d--; if ($d -eq 0) { break } }
                        $e++
                    }
                    if ($e -ge $len) { break }
                    [void]$out.Add(((" " * ($depth * 2)) + $Json.Substring($k, $e - $k + 1)))
                    $i = $e + 1
                }
            }
            continue
        }

        # Depth is the nesting of the children arrays, which is what restores
        # the parent/sibling structure the rest of the script reads.
        if ($c -eq '[') { $depth++; $i++; continue }
        if ($c -eq ']') { $depth--; $i++; continue }
        $i++
    }
    return ,$out.ToArray()
}

function Get-UiTestTree {
    param([int] $Id = 0)
    $path = "/data/local/tmp/ark_layout.json"
    Invoke-HdcRaw "shell `"uitest dumpLayout -p $path`"" | Out-Null
    $raw = @(Invoke-HdcRaw "shell `"cat $path`"")
    if ($raw.Count -eq 0) {
        Write-Host "uitest produced nothing. Check:  hdc shell uitest dumpLayout -p $path" -ForegroundColor Yellow
        return @()
    }
    $joined = ($raw -join "")
    if ($joined -notmatch '"attributes"') {
        Write-Host "uitest answered but the output is not a layout dump:" -ForegroundColor Yellow
        Write-Host ("  " + $joined.Substring(0, [math]::Min(160, $joined.Length))) -ForegroundColor DarkGray
        return @()
    }
    $flat = ConvertFrom-UiTestLayout -Json $joined
    return (Select-UiTestWindow -Lines $flat -Id $Id)
}

# uitest dumps the WHOLE DISPLAY, not one window, so its nodes have to be
# attributed before a per-window classifier sees them - otherwise the app, the
# shell and the keyboard merge into one tree.
#
# How they are attributed differs by build. Some emit a list of window roots
# each carrying hostWindowId; some carry it on inner nodes only; some do not
# emit it at all, and then the dump is simply whatever was in front of the
# user. All three happen, all three are handled, and which one happened is
# reported rather than assumed.
function Select-UiTestWindow {
    param([string[]] $Lines, [int] $Id)

    if (-not $Lines -or $Lines.Count -eq 0) { return ,@() }

    $winPat = '(?i)"(?:host[_ ]?window[_ ]?id|window[_ ]?id|winId)"\s*:\s*"?(\d+)'

    $indent = New-Object int[] $Lines.Count
    $owner  = New-Object int[] $Lines.Count
    $anyWin = $false
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        $indent[$i] = $Lines[$i].Length - $Lines[$i].TrimStart().Length
        $m = [regex]::Match($Lines[$i], $winPat)
        if ($m.Success) { $owner[$i] = [int]$m.Groups[1].Value; $anyWin = $true }
        else { $owner[$i] = -1 }
    }

    if (-not $anyWin) {
        # Nothing to attribute by. That is NOT the same as "every window
        # merged into one tree": it is one dump with no window labelling,
        # which on a single-app screen is the whole truth and on a layered one
        # is a limitation. Saying which is the difference between a warning
        # worth acting on and noise.
        $script:UiTestWindows = @("no window attribute in this dump")
        $script:UiTestScope = "UNLABELLED"
        return ,$Lines
    }

    # Inherit downward: a node without the attribute belongs to the nearest
    # ancestor that has one.
    $stack = @{}
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($owner[$i] -ge 0) { $stack[$indent[$i]] = $owner[$i]; continue }
        $best = -1; $bestInd = -1
        foreach ($k in @($stack.Keys)) {
            if ($k -lt $indent[$i] -and $k -gt $bestInd) { $bestInd = $k; $best = $stack[$k] }
        }
        $owner[$i] = $best
    }

    $counts = @{}
    foreach ($o in $owner) {
        if ($o -lt 0) { continue }
        if ($counts.ContainsKey($o)) { $counts[$o] = $counts[$o] + 1 } else { $counts[$o] = 1 }
    }
    $script:UiTestWindows = @($counts.GetEnumerator() | Sort-Object Value -Descending |
                              ForEach-Object { "{0}:{1}" -f $_.Key, $_.Value })

    $keep = @()
    for ($i = 0; $i -lt $Lines.Count; $i++) { if ($owner[$i] -eq $Id) { $keep += $Lines[$i] } }
    if ($keep.Count -eq 0) {
        $script:UiTestScope = "ALL"
        return ,$Lines
    }
    $script:UiTestScope = "WIN"
    return ,$keep
}

# ------------------------------------------------------------------ geometry
#
# -Screen cannot differ from -Window without node rects, and on this build the
# inspector view carries none. That is a property of ONE dump option, not of
# the device: the layout results exist, something just has to print them. This
# tries every view and reports which one does, so the question is settled by a
# single command instead of a guess.
function Show-FindRects {
    param([int] $Id)

    $rectPat = '(?i)(?:\$?rect|frameRect|bounds)\s*[:=]|\[\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\]|\b\d{2,5}(?:\.\d+)?\s*[xX*]\s*\d{2,5}'

    Write-Host ""
    Write-Host ("GEOMETRY SEARCH  window {0}" -f $Id) -ForegroundColor Cyan
    Write-Host "  which hidumper view carries node rects on this build" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host ("  {0,-12} {1,8} {2,8} {3,8}  {4}" -f "view", "lines", "nodes", "rects", "verdict")

    $best = ""; $bestN = 0
    foreach ($opt in @('inspector','render','element','frontend','navigation','uitest')) {
        $lines = if ($opt -eq 'uitest') { @(Get-UiTestTree -Id $Id) }
                 else { @(Invoke-HdcRaw "shell `"hidumper -s WindowManagerService -a '-w $Id -$opt'`"") }
        $body  = @($lines | Where-Object { $_ -and $_.Trim() })
        if ($body.Count -eq 0) {
            Write-Host ("  {0,-12} {1,8} {2,8} {3,8}  {4}" -f $opt, 0, "-", "-", "no output") -ForegroundColor DarkGray
            continue
        }
        $nodes = Get-Nodes -Lines $lines
        $boxes = @($nodes | Where-Object { $_.Rect }).Count
        # Count rect-shaped TEXT too: a view may carry geometry the node
        # assembler does not attach, and that is a parser fix, not a dead end.
        $rawHits = @($body | Where-Object { $_ -match $rectPat }).Count

        $verdict = if ($boxes -ge 5) { "USE THIS" }
                   elseif ($rawHits -ge 5) { "has rects, parser misses them" }
                   else { "no geometry" }
        $col = if ($boxes -ge 5) { "Green" } elseif ($rawHits -ge 5) { "Yellow" } else { "DarkGray" }
        Write-Host ("  {0,-12} {1,8} {2,8} {3,8}  {4}" -f $opt, $body.Count, $nodes.Count, $boxes, $verdict) -ForegroundColor $col
        if ($rawHits -gt 0 -and $boxes -lt 5) {
            $sample = @($body | Where-Object { $_ -match $rectPat } | Select-Object -First 2)
            foreach ($l in $sample) { Write-Host ("               {0}" -f $l.Trim().Substring(0, [math]::Min(96, $l.Trim().Length))) -ForegroundColor DarkGray }
        }
        if ($boxes -gt $bestN) { $bestN = $boxes; $best = $opt }
    }

    Write-Host ""
    if ($best) {
        Write-Host ("  use it:  scene_class.cmd -Window -Screen -DumpOpt {0}" -f $best) -ForegroundColor Green
    } else {
        Write-Host "  no view carried rects the parser could attach." -ForegroundColor Yellow
        Write-Host "  Where a line is shown above, the geometry IS there and the rect parser" -ForegroundColor DarkGray
        Write-Host "  needs the format - that is a small fix. Otherwise try -RsProbe: RS has" -ForegroundColor DarkGray
        Write-Host "  layer bounds because it cannot composite without them." -ForegroundColor DarkGray
    }
}

# ---------------------------------------------------------------- RenderService
#
# The inspector dump not printing a property does NOT mean the property does
# not exist. The live FrameNode has it; the serialiser simply did not write it.
# That distinction matters because it decides whether a signal is missing or
# merely unreachable THROUGH THIS CHANNEL - and render_service is a different
# channel, on the far side of the surface, which is exactly where the things
# ArkUI cannot see are kept.
#
# This is discovery, not a feature: nobody here knows what this build's RS
# accepts, so the candidate list is tried and the ones that answer are
# reported. Mutating arguments (trimMem, dumpMem and friends) are deliberately
# NOT in the list - they change the thing we are measuring.
$script:RsProbeArgs = @(
    @{ A = 'screen';         W = 'display config: resolution, refresh rate, rotation, HDR' }
    @{ A = 'surface';        W = 'layer list: names, bounds, buffer state, z. The join we want' }
    @{ A = 'RSTree';         W = 'the render tree - one node per line, the global composition' }
    @{ A = 'nodeNotOnTree';  W = 'allocated but not composited - cost without pixels' }
    @{ A = 'allSurfacesMem'; W = 'per-surface memory; sizes imply buffer format and count' }
    @{ A = 'composer fps';   W = 'composition rate for the display' }
    @{ A = 'allInfo';        W = 'everything RS will print, usually long' }
)

function Invoke-RsDump {
    param([string] $Arg)
    return Invoke-HdcRaw "shell `"hidumper -s RenderService -a '$Arg'`""
}

function Show-RsProbe {
    param([int] $Preview = 6)

    Write-Host ""
    Write-Host "RENDER SERVICE PROBE  [R]" -ForegroundColor Cyan
    Write-Host "  which dump arguments answer on this build, and what they carry." -ForegroundColor DarkGray
    Write-Host "  read-only arguments only - nothing here trims or frees anything." -ForegroundColor DarkGray

    $worked = @()
    foreach ($p in $script:RsProbeArgs) {
        $out = @(Invoke-RsDump -Arg $p.A | Where-Object { $_ -and $_.Trim() })
        $bad = ($out.Count -eq 0) -or
               (@($out | Where-Object { $_ -match '(?i)unknown|not support|invalid|no such|usage:|permission' }).Count -gt 0 -and $out.Count -lt 4)
        Write-Host ""
        if ($bad) {
            Write-Host ("  {0,-16} no" -f $p.A) -ForegroundColor DarkGray
            if ($out.Count -gt 0) { Write-Host ("    {0}" -f $out[0].Trim()) -ForegroundColor DarkGray }
            continue
        }
        $worked += $p.A
        Write-Host ("  {0,-16} YES  {1} lines   ({2})" -f $p.A, $out.Count, $p.W) -ForegroundColor Green
        foreach ($l in ($out | Select-Object -First $Preview)) { Write-Host ("    {0}" -f $l.Trim()) -ForegroundColor DarkGray }
        if ($out.Count -gt $Preview) { Write-Host ("    ... {0} more" -f ($out.Count - $Preview)) -ForegroundColor DarkGray }
    }

    Write-Host ""
    if ($worked.Count -eq 0) {
        Write-Host "  nothing answered. Either the service is named differently here -" -ForegroundColor Yellow
        Write-Host "  run -Services and look for a render entry - or it needs more rights." -ForegroundColor Yellow
    } else {
        Write-Host ("  answered: {0}" -f ($worked -join ", ")) -ForegroundColor Green
        Write-Host "  if 'surface' answered, its layer names are the join key the component" -ForegroundColor DarkGray
        Write-Host "  dump is missing, and -RsFps <layer name> gives that layer's submit rate." -ForegroundColor DarkGray
    }
}

# Per-layer submit rate. This is the one measurement that settles what an
# opaque node is doing rather than what it declares: a layer submitting at
# 60/s is live content, one submitting at 0 is a paused player or a static
# map. ArkUI cannot see it at all - those frames never pass through the
# pipeline - so unlike almost everything else here, this signal belongs to
# the aggregator by necessity, not by choice.
function Show-RsFps {
    param([string] $Layer)
    Write-Host ""
    Write-Host ("LAYER SUBMIT RATE  [R]   '{0}'" -f $Layer) -ForegroundColor Cyan
    $out = @(Invoke-RsDump -Arg ("fps " + $Layer) | Where-Object { $_ -and $_.Trim() })
    if ($out.Count -eq 0) {
        Write-Host "  nothing returned - check the name against the 'surface' dump" -ForegroundColor Yellow
        return
    }
    foreach ($l in $out) { Write-Host ("  {0}" -f $l.Trim()) }
}

# Lines from the RS surface/tree dumps that plausibly belong to this window.
# Matched on pid and on the window name, because RS names layers after the
# window and the two dumps do not share an id format.
function Get-RsForWindow {
    param([string] $WinName, [string] $ProcId)

    $hits = @()
    foreach ($arg in @('surface', 'RSTree')) {
        $out = @(Invoke-RsDump -Arg $arg | Where-Object { $_ -and $_.Trim() })
        if ($out.Count -eq 0) { continue }
        foreach ($l in $out) {
            $t = $l.Trim()
            if ($ProcId -and $t -match ("\b" + [regex]::Escape($ProcId) + "\b")) { $hits += "[$arg] $t"; continue }
            if ($WinName -and $t -match [regex]::Escape($WinName))               { $hits += "[$arg] $t" }
        }
    }
    return ,@($hits | Select-Object -Unique | Select-Object -First 24)
}

# The classifier, run against one scope and labelled as such. Separate from
# Invoke-Classify because that one is the CSV path - it stamps timestamps,
# tracks churn across ticks and writes rows. This just answers the question
# for the tree in front of it.
function Show-ScopeClass {
    param($F, [string] $Mode, $Win)

    $R = Get-SceneClass -F $F
    $churn = [PSCustomObject]@{ Delta=0; Added=0; Removed=0; Net=0; Shape="NONE"; Rate=0.0; Dt=0.0; First=$true }
    $mods = Get-Modifiers -F $F -Churn $churn -Win $Win
    # In window scope the clip is off BY REQUEST, so GEO_UNTRUSTED here would
    # report a deliberate choice as a defect - and the same flag means a real
    # problem everywhere else, which is exactly how a modifier stops being
    # worth reading.
    if ($Mode -eq "window") { $mods = @($mods | Where-Object { $_ -ne "GEO_UNTRUSTED" }) }
    $col = switch ($R.Conf) { "structural" {"Green"} "weak" {"Yellow"} default {"DarkYellow"} }

    Write-Host ""
    Write-Host ("  CLASS      {0}   ({1} as a {2})" -f $R.Class, $R.Conf, $Mode) -ForegroundColor $col
    if ($R.Cand.Count -gt 0) { Write-Host ("  candidates {0}" -f ($R.Cand -join ", ")) }
    Write-Host ("  ranked     {0}" -f $R.Ranked)
    Write-Host ("  evidence   {0}" -f ($R.Ev -join "; "))
    if ($mods.Count -gt 0) { Write-Host ("  modifiers  {0}" -f ($mods -join " | ")) }
}

# ---------------------------------------------------------- window properties
#
# Window flags say something about content without looking at it. An app asks
# for keep-screen-on because it knows the user is watching something, and that
# is a stronger statement than any name match on a component id.
#
# Mined generically: the keys differ across builds and we do not know this
# one, so everything is kept and the interesting names are merely promoted -
# the same principle as the unclaimed attributes on a node.
function Get-WindowProps {
    param([int] $Id)
    $lines = Invoke-HdcRaw "shell `"hidumper -s WindowManagerService -a '-w $Id -element'`""
    $kv = [ordered]@{}
    foreach ($l in $lines) {
        foreach ($m in [regex]::Matches($l, '([A-Za-z_][A-Za-z0-9_ ]{2,31}?)\s*[:=]\s*([^,;|}\s][^,;|}]{0,46})')) {
            $k = $m.Groups[1].Value.Trim()
            $v = $m.Groups[2].Value.Trim()
            if ($k -and $v -and -not $kv.Contains($k)) { $kv[$k] = $v }
        }
    }
    return $kv
}

# Window flags that say something about content without looking at it. These
# are declarations of intent - an app asks for keep-screen-on because it knows
# the user is watching something, and that is a stronger statement than any
# name match on a component id.
$script:WindowPropsOfInterest = '(?i)keep.?screen|brightness|orientation|privacy|secure|touchable|focusable|transparent|decor|window.?mode|window.?type|pip|float|split|display.?id|visib|alpha|scale|dirty|flag'


function Get-Features {
    # -NoClip computes the same features over the WHOLE tree, ignoring the
    # viewport even when it is trusted. That is what -Window means, and the
    # classifier needs its own feature set for each scope: a window holding a
    # player and a screen showing a list are two different answers from one
    # dump, and they cannot share one count.
    param([string[]] $Lines, [switch] $NoClip)

    $nodes = Get-Nodes -Lines $Lines

    # ---- viewport ------------------------------------------------------
    # A scroller's own rect is clipped to the viewport; its CHILDREN are not,
    # so a long page carries rows that are laid out but not rendered. Dropping
    # those is the point of this block.
    #
    # But a wrong viewport drops EVERYTHING, and a classifier fed an empty
    # tree does not fail loudly - it quietly answers LIST for every screen.
    # So the clip only runs when the viewport is CORROBORATED against the
    # panel size read from the window manager. No corroboration, no clip: the
    # counts fall back to the whole tree, which is merely less precise.
    $vp = $null; $vpTrusted = $false

    # First choice: the window's OWN rect, read from the window manager. It is
    # not a guess and it is not a node, which matters - picking the tree node
    # whose rect best resembles the panel quietly excluded whatever sat below
    # that node, and on both a launcher and a video app that was the bottom
    # bar. Five icons counted as "scrolled out" while plainly on screen.
    $script:VpImeClipped = $false
    if ($script:WinRect -and $script:WinRect.W -gt 1 -and $script:WinRect.H -gt 1) {
        $vp = @{ L = [double]$script:WinRect.X; T = [double]$script:WinRect.Y
                 R = [double]($script:WinRect.X + $script:WinRect.W)
                 B = [double]($script:WinRect.Y + $script:WinRect.H) }
        # ...stopping at the keyboard. The window's rect does not shrink when
        # the IME opens - the IME is a separate window drawn over it - so the
        # bottom 40% of that rect is not screen the user can see, and every
        # position normalised against it is wrong by that much.
        if ($script:ImeTop -gt $vp.T + 1 -and $script:ImeTop -lt $vp.B) {
            $vp.B = $script:ImeTop
            $script:VpImeClipped = $true
        }
        # The IME host window on this build reports the WHOLE PANEL as its
        # rect - SCBKeyboardPanel at 0,0 1316x2832 - so the keyboard's top
        # edge is not in the window table to be read. It is in this window's
        # own layout instead: ArkUI moved the content up to avoid the
        # keyboard, so the lowest real element in the tree now sits just above
        # it. Background-sized nodes are excluded, or the root would answer
        # the question with the panel height it always has.
        if (-not $script:VpImeClipped -and ($script:SceneContext -contains "IME_UP")) {
            $winH = $vp.B - $vp.T; $winW = $vp.R - $vp.L
            # NOT the lowest node: the keyboard's own nodes are in this dump
            # too - 176 nodes with the IME up against 142 without - so the
            # lowest node is a key at the bottom of the panel and nothing is
            # ever clipped. What marks the keyboard's top edge is the app's
            # content container, which ArkUI shrank to avoid it: full width,
            # starting at the top, ending mid-panel. Take the largest such
            # box and clip to ITS bottom.
            $best = $null; $bestA = 0.0
            foreach ($n in $nodes) {
                if (-not $n.Rect) { continue }
                $w = $n.Rect.R - $n.Rect.L; $h = $n.Rect.B - $n.Rect.T
                if ($w -lt $winW * 0.9) { continue }
                if ($h -lt $winH * 0.3 -or $h -ge $winH * 0.95) { continue }
                if ($n.Rect.T -gt $vp.T + $winH * 0.15) { continue }
                if (($w * $h) -gt $bestA) { $bestA = $w * $h; $best = $n }
            }
            if ($best -and $best.Rect.B -gt ($vp.T + $winH * 0.4) -and $best.Rect.B -lt ($vp.B - 1)) {
                $vp.B = $best.Rect.B
                $script:VpImeClipped = $true
            }
        }
        $vpTrusted = $true
    }

    if (-not $vp -and $script:ScreenW -gt 0) {
        # the rect closest to the panel, within 15% on both axes
        $bestErr = 1e9
        foreach ($n in $nodes) {
            if (-not $n.Rect) { continue }
            $w = $n.Rect.R - $n.Rect.L; $h = $n.Rect.B - $n.Rect.T
            if ($w -lt $script:ScreenW * 0.85 -or $w -gt $script:ScreenW * 1.15) { continue }
            if ($h -lt $script:ScreenH * 0.4  -or $h -gt $script:ScreenH * 1.15) { continue }
            $err = [math]::Abs($w - $script:ScreenW) + [math]::Abs($h - $script:ScreenH)
            if ($err -lt $bestErr) { $bestErr = $err; $vp = $n.Rect }
        }
        if ($vp) { $vpTrusted = $true }
    }
    if (-not $vp) {
        # Largest rect in the dump. Used for overdraw and largest-leaf ratios,
        # never for clipping, because nothing confirms it is the screen.
        $best = -1.0
        foreach ($n in $nodes) {
            if (-not $n.Rect) { continue }
            $a2 = ($n.Rect.R - $n.Rect.L) * ($n.Rect.B - $n.Rect.T)
            if ($a2 -gt $best) { $best = $a2; $vp = $n.Rect }
        }
    }
    $geoOK = $false
    if ($vp) {
        $vpW = $vp.R - $vp.L; $vpH = $vp.B - $vp.T
        if ($vpW -gt 1 -and $vpH -gt 1) { $geoOK = $true }
    }
    if (-not $geoOK) { $vpW = 0.0; $vpH = 0.0; $vpTrusted = $false }
    if ($NoClip) { $vpTrusted = $false }
    $vpArea = $vpW * $vpH

    # ---- visibility ------------------------------------------------------
    # ONLY an explicit visibility state. An earlier version also excluded
    # `active: false` and `visible: false`, which different builds use for
    # things that are on screen perfectly well.
    $hiddenPat = '(?i)visibility\s*[:=.]\s*"?(Hidden|None|GONE|INVISIBLE)\b'
    $hidden = 0
    foreach ($n in $nodes) { if ($n.Text -match $hiddenPat) { $hidden++ } }
    # If it wants to remove a third of the tree it has matched something other
    # than a visibility state. Drop the filter rather than the screen.
    $useHidden = ($hidden -lt ($nodes.Count * 0.3))
    if (-not $useHidden) { $hidden = 0 }

    $vis = New-Object System.Collections.ArrayList
    $off = 0; $boxes = 0
    $areaSum = 0.0
    $bigLeafArea = 0.0; $bigLeafW = 0.0; $bigLeafH = 0.0
    foreach ($n in $nodes) {
        if ($useHidden -and $n.Text -match $hiddenPat) { continue }
        if (-not $n.Rect) { [void]$vis.Add($n); continue }
        $boxes++
        $r = $n.Rect
        $cl = [math]::Max($r.L, $vp.L); $ct = [math]::Max($r.T, $vp.T)
        $cr = [math]::Min($r.R, $vp.R); $cb = [math]::Min($r.B, $vp.B)
        $w = $cr - $cl; $h = $cb - $ct
        if ($w -le 0.5 -or $h -le 0.5) {
            $off++
            if ($vpTrusted) { continue }      # scrolled out
            $w = 0.0; $h = 0.0
        }
        $n.VisW = $w; $n.VisH = $h
        if ($w -gt 0) { $n.CenterY = ($ct + $cb) / 2.0 }
        $areaSum += ($w * $h)
        if ($n.Leaf -and ($w * $h) -gt $bigLeafArea) {
            $bigLeafArea = $w * $h; $bigLeafW = $w; $bigLeafH = $h
        }
        [void]$vis.Add($n)
    }

    # Last line of defence. If the clip somehow still emptied the tree, the
    # answer is not to classify an empty tree - it is to stop clipping.
    if ($vpTrusted -and $nodes.Count -ge 20 -and $vis.Count -lt ($nodes.Count * 0.15)) {
        $vpTrusted = $false
        $vis = New-Object System.Collections.ArrayList
        foreach ($n in $nodes) { [void]$vis.Add($n) }
        $script:ClipAborted = $true
    } else { $script:ClipAborted = $false }

    # ---- counts, over what is on screen ----------------------------------
    $tally = @{}; $tallyAll = @{}
    foreach ($n in $nodes) { if ($tallyAll.ContainsKey($n.Tag)) { $tallyAll[$n.Tag]++ } else { $tallyAll[$n.Tag] = 1 } }
    foreach ($n in $vis)   { if ($tally.ContainsKey($n.Tag))    { $tally[$n.Tag]++ }    else { $tally[$n.Tag] = 1 } }
    function C($n) { if ($tally.ContainsKey($n)) { [int]$tally[$n] } else { 0 } }
    $total = 0; foreach ($v in $tally.Values) { $total += $v }

    # ---- text ------------------------------------------------------------
    # avg per text-bearing node is the label-vs-snippet discriminator: a
    # settings row and a feed card both have "text", 12 chars against 90.
    # A switch is a TYPE in the inspector and an ATTRIBUTE in uitest: the
    # accessibility model describes a toggle as a node that is checkable, not
    # as a component called Toggle. Counting only the type reported zero
    # switches on every Settings page on the device, which is why the rule
    # that was supposed to find a preferences page never fired once.
    # A row with a thumbnail on the left and a card with a banner across it
    # have the same image COUNT and completely different cost: one decodes a
    # 100px avatar per row, the other a full-width bitmap. Counts cannot tell
    # them apart; position and width can, now that there are rects.
    $iconLeft = 0; $wideImg = 0; $imgGeo = 0
    if ($vpW -gt 1) {
        foreach ($n in $vis) {
            if ($n.Tag -notmatch '^(Image|SymbolGlyph|Symbol|ImageSpan|ImageAnimator)$') { continue }
            if (-not $n.Rect) { continue }
            $imgGeo++
            $w = $n.Rect.R - $n.Rect.L
            $cx = ($n.Rect.L + $n.Rect.R) / 2.0 - $vp.L
            if ($w -ge ($vpW * 0.5)) { $wideImg++ }
            elseif ($w -le ($vpW * 0.2) -and $cx -le ($vpW * 0.3)) { $iconLeft++ }
        }
    }

    # How much of the viewport is covered by regions ArkUI cannot look into.
    # This is the record's own statement of how complete it is: at 20 permille
    # the native counts are the whole story, at 900 they describe the chrome
    # around a hole, and the scene the user is looking at may be drawn ENTIRELY
    # inside that hole. A label given to such a screen describes pixels this
    # tree never contained.
    # An opaque node is only a HOLE if the dump STOPS at it. uitest projects
    # accessibility, and accessibility has to cross an embedded-UI boundary or
    # the content inside would be unreachable - so for an EmbeddedComponent or
    # a UIExtensionComponent the nodes inside it are in this dump, with rects,
    # and the region is described rather than hidden. Counting it anyway put
    # "952 permille of this screen is behind a surface" on a chat whose
    # avatars and composer the same pass had just read, and told the labeller
    # the row was worthless.
    #
    # Descendants in the TREE, not nodes inside the RECT. A seekbar drawn over
    # a video sits inside the XComponent's rect and is not in its subtree, and
    # that distinction is exactly the difference between chrome ON a surface
    # and a surface whose tree we actually have.
    # ...and the same question the other way round. The coverage pass asks how
    # much of the VIEWPORT is behind a surface. It never asked how much of the
    # SURFACE is behind native nodes, so a video with a comments sheet over
    # its lower half read exactly like a video playing fullscreen: one opaque
    # node at 952 permille, seekbar low, MEDIA_PLAYER structural. Both are
    # true at once and only the second number separates them.
    #
    # Occlusion inside the tree, by the same coordinate compression the window
    # pass uses. Paint order is document order - a later node is drawn over an
    # earlier one - so the candidates are the nodes that come after the
    # surface's whole subtree, which is also what makes them not its children.
    # Candidates covering less than half a percent are dropped and ones
    # entirely inside a larger candidate are skipped, which keeps the grid at
    # a couple of dozen rects however many nodes the screen has.
    #
    # It is an UPPER bound, for the same reason the window pass is: nothing in
    # the dump says whether a node actually paints anything. A transparent
    # container over the video counts as covering it.
    $described = 0
    $surfCovered = 0.0; $surfArea = 0.0
    if ($nodes.Count -gt 0) {
        for ($i = 0; $i -lt $nodes.Count; $i++) {
            $nn = $nodes[$i]
            if ($nn.Tag -notmatch $script:OpaquePat) { continue }
            $inner = 0
            $end = $i + 1
            while ($end -lt $nodes.Count -and $nodes[$end].Indent -gt $nn.Indent) {
                if ($nodes[$end].Rect) { $inner++ }
                $end++
            }
            $nn.Inner = $inner
            if ($inner -ge 3) { $described++ }

            if (-not $nn.Rect) { continue }
            $oL = $nn.Rect.L; $oT = $nn.Rect.T; $oR = $nn.Rect.R; $oB = $nn.Rect.B
            $oA = ($oR - $oL) * ($oB - $oT)
            if ($oA -le 1 -or $oA -lt $surfArea) { continue }

            $kept = @()
            $cands = @()
            for ($j = $end; $j -lt $nodes.Count; $j++) {
                $cn = $nodes[$j]
                if (-not $cn.Rect) { continue }
                $il = [math]::Max($oL, $cn.Rect.L); $it = [math]::Max($oT, $cn.Rect.T)
                $ir = [math]::Min($oR, $cn.Rect.R); $ib = [math]::Min($oB, $cn.Rect.B)
                if ($ir -le $il -or $ib -le $it) { continue }
                $ia = ($ir - $il) * ($ib - $it)
                if ($ia -lt ($oA * 0.005)) { continue }
                $cands += [PSCustomObject]@{ L=$il; T=$it; R=$ir; B=$ib; A=$ia }
            }
            foreach ($q in ($cands | Sort-Object A -Descending)) {
                $swallowed = $false
                foreach ($k2 in $kept) {
                    if ($k2.L -le $q.L -and $k2.T -le $q.T -and $k2.R -ge $q.R -and $k2.B -ge $q.B) { $swallowed = $true; break }
                }
                if ($swallowed) { continue }
                $kept += $q
                if ($kept.Count -ge 24) { break }
            }

            $cov = 0.0
            if ($kept.Count -gt 0) {
                $xs = @(@($oL, $oR) + @($kept | ForEach-Object { $_.L; $_.R }) | Sort-Object -Unique)
                $ys = @(@($oT, $oB) + @($kept | ForEach-Object { $_.T; $_.B }) | Sort-Object -Unique)
                for ($a = 0; $a -lt $xs.Count - 1; $a++) {
                    $mx = ([double]$xs[$a] + [double]$xs[$a+1]) / 2.0
                    for ($b2 = 0; $b2 -lt $ys.Count - 1; $b2++) {
                        $my = ([double]$ys[$b2] + [double]$ys[$b2+1]) / 2.0
                        foreach ($q in $kept) {
                            if ($mx -ge $q.L -and $mx -lt $q.R -and $my -ge $q.T -and $my -lt $q.B) {
                                $cov += ([double]$xs[$a+1] - [double]$xs[$a]) * ([double]$ys[$b2+1] - [double]$ys[$b2])
                                break
                            }
                        }
                    }
                }
            }
            $surfArea = $oA
            $surfCovered = $cov / $oA
        }
    }

    $coverWeb = 0.0; $coverXc = 0.0; $coverEmb = 0.0
    if ($vpArea -gt 1) {
        foreach ($n in $vis) {
            if ($n.Tag -notmatch $script:OpaquePat) { continue }
            # Not a hole: we have its tree.
            if ($n.Inner -ge 3) { continue }
            $a = 0.0
            if ($n.VisW -gt 0 -and $n.VisH -gt 0) { $a = $n.VisW * $n.VisH }
            elseif ($n.Rect) {
                $a = [math]::Max(0.0, ($n.Rect.R - $n.Rect.L)) * [math]::Max(0.0, ($n.Rect.B - $n.Rect.T))
            }
            if ($a -le 0) { continue }
            # Nested surfaces would double count, so each is clamped to the
            # viewport and the total is clamped again below.
            $f = [math]::Min(1.0, $a / $vpArea)
            # Three destinations, not two. A Web region is recoverable in
            # this process by the ArkWeb producer. An XComponent is a buffer
            # queue and is outside ArkUI permanently. An EmbeddedComponent or
            # UIExtensionComponent is NEITHER: it is another ArkUI instance,
            # in another process, with its own pipeline - so the same
            # collector running THERE describes it exactly, and the hole is
            # closed by routing rather than by a different kind of producer.
            # Lumping it in with XComponent said "permanently outside" about
            # a region that is the easiest of the three to recover.
            if     ($n.Tag -eq 'Web')                { $coverWeb += $f }
            elseif ($n.Tag -match $script:EmbedPat)  { $coverEmb += $f }
            else                                     { $coverXc  += $f }
        }
    }
    $coverWeb = [math]::Min(1.0, $coverWeb)
    $coverXc  = [math]::Min(1.0, $coverXc)
    $coverEmb = [math]::Min(1.0, $coverEmb)
    $coverOpaque = [math]::Min(1.0, $coverWeb + $coverXc + $coverEmb)

    $checkable = 0
    foreach ($n in $vis) {
        if ($n.Text -match '(?i)"checkable"\s*:\s*"?true') { $checkable++ }
    }

    $textLen = 0; $textMax = 0; $textNodes = 0
    foreach ($n in $vis) {
        # The inspector calls it "content", uitest calls it "text". Both, and
        # the empty-string case excluded, because uitest emits "text":"" on
        # every non-text node and counting those would halve every average.
        $m = [regex]::Match($n.Text, '(?i)"?\b(?:content|text)"?\s*[:=]\s*"([^"]{2,})"')
        if (-not $m.Success) { $m = [regex]::Match($n.Text, '(?i)\bcontent\s*[:=]\s*"?([^"|,]{2,})') }
        if ($m.Success) {
            $len = $m.Groups[1].Value.Trim().Length
            $textLen += $len; $textNodes++
            if ($len -gt $textMax) { $textMax = $len }
        }
    }
    $avgText = if ($textNodes -gt 0) { [math]::Round($textLen / $textNodes, 1) } else { 0.0 }

    # ---- position, within the viewport -----------------------------------
    # A search field sits ABOVE the list, a chat composer BELOW it. Normalised
    # against the viewport, not the document, so a long page does not push the
    # composer to 0.05 just because the content behind it is tall.
    function MeanPos($tags) {
        if (-not $geoOK) { return -1.0 }
        $s = 0.0; $k = 0
        foreach ($n in $vis) {
            if ($tags -contains $n.Tag -and $n.CenterY -ne $null) {
                $s += (($n.CenterY - $vp.T) / $vpH); $k++
            }
        }
        if ($k -eq 0) { return -1.0 }
        return [math]::Round($s / $k, 3)
    }
    $editPos   = MeanPos @('TextInput','TextArea','Search','RichEditor')
    $sliderPos = MeanPos @('Slider')
    $posSource = if (-not $geoOK) { "none" } elseif ($vpTrusted) { "viewport" } else { "largest-rect" }

    # ---- render-cost multipliers, on visible nodes only ------------------
    $fxBlur=0; $fxShadow=0; $fxOpacity=0; $fxClip=0; $fxGrad=0
    $declRate=0; $lazy=0; $reusable=0; $cached=0
    # uitest emits a FIXED attribute set per node, so every node carries a
    # "clip" key and a "blur" key whether or not anything is set. The scan was
    # written for the inspector dump, where an attribute appears only when it
    # has a value, and under uitest it matched all 184 nodes: fx 1840, blur
    # 184, clip 184, FX_HEAVY on a screen with no effects at all. A key is not
    # a value. Where the dump gives "key": value, read the value; where it
    # gives a bare word, keep the old behaviour.
    $fxOn = {
        param([string] $t, [string] $key)
        # Any KEY CONTAINING the word, not the bare word. Matching "blur"
        # exactly found no key at all on this build - uitest spells it
        # backgroundBlurStyle / foregroundBlurStyle - so the fallback fired
        # and matched the word inside the key name itself, on all 176 nodes.
        # clip got fixed and blur did not, which is what gave it away.
        $ms = [regex]::Matches($t, ('(?i)"([a-z0-9_]*' + $key + '[a-z0-9_]*)"\s*:\s*"?([^",}\]]*)'))
        if ($ms.Count -gt 0) {
            foreach ($m in $ms) {
                $v = $m.Groups[2].Value.Trim()
                # Anywhere in the value, not anchored: a style enum spells
                # off as BlurStyle.NONE or NoMaterial, and an anchored test
                # reads both as on.
                if ($v -ne "" -and $v -notmatch '(?i)(false|none|null|unset|default|^no$)' -and
                    $v -notmatch '^0+(\.0+)?$') { return $true }
            }
            return $false
        }
        # No JSON keys at all means the inspector dump, where an attribute is
        # written only when it has been set. There the bare word is the signal.
        if ($t -match '"\s*:\s*') { return $false }
        return ($t -match ('(?i)' + $key))
    }
    foreach ($n in $vis) {
        $t = $n.Text
        if (& $fxOn $t 'blur')                     { $fxBlur++ }
        if (& $fxOn $t 'shadow')                   { $fxShadow++ }
        if (& $fxOn $t 'opacity')                  { $fxOpacity++ }
        if ((& $fxOn $t 'clip') -or (& $fxOn $t 'mask')) { $fxClip++ }
        if (& $fxOn $t 'gradient')                 { $fxGrad++ }
        if ($t -match '(?i)lazyforeach')           { $lazy++ }
        if ($t -match '(?i)reusable|recycle')      { $reusable++ }
        $m = [regex]::Match($t, '(?i)expectedFrameRate\s*[:=]\s*"?(\d+)')
        if ($m.Success) { $r = [int]$m.Groups[1].Value; if ($r -gt $declRate) { $declRate = $r } }
        $m = [regex]::Match($t, '(?i)cachedCount\s*[:=]\s*"?(\d+)')
        if ($m.Success) { $cached += [int]$m.Groups[1].Value }
    }
    $fxScore = ($fxBlur * 8) + ($fxShadow * 2) + $fxOpacity + $fxClip + $fxGrad

    # ---- what is behind the opaque regions -------------------------------
    $opaqueNodes = @()
    foreach ($n in $vis) { if ($n.Tag -match $script:OpaquePat) { $opaqueNodes += $n } }
    $pre = [PSCustomObject]@{ Slider = (C 'Slider'); Button = (C 'Button') }
    $xc = if ($opaqueNodes.Count -gt 0) { Get-OpaqueHint -Nodes $opaqueNodes -F $pre }
          else { [PSCustomObject]@{ Name=""; Lib=""; XType=""; A11y=""; Secure=0; Hdr=0
                                    Aspect=0.0; Hint="NONE"; Conf=0; Flags=""; Why="" } }

    [PSCustomObject]@{
        Tally      = $tally          # visible only - churn tracks the screen
        TallyAll   = $tallyAll
        Nodes      = $nodes.Count
        NodeList   = $nodes
        ParseMode  = $script:ParseMode
        Total      = $total
        OffScreen  = $off
        Hidden     = $hidden
        GeoOK      = $geoOK
        VpTrusted  = $vpTrusted
        ClipAborted = $script:ClipAborted
        ScreenW    = $script:ScreenW
        ScreenH    = $script:ScreenH
        VpW        = [int]$vpW
        VpH        = [int]$vpH
        Text       = (C 'Text') + (C 'Span') + (C 'RichText')
        Image      = (C 'Image')
        Icon       = (C 'SymbolGlyph') + (C 'Symbol') + (C 'ImageSpan') + (C 'ImageAnimator')
        Button     = (C 'Button')
        Slider     = (C 'Slider')
        Progress   = (C 'Progress') + (C 'LoadingProgress')
        # Deliberately NOT folded together with $checkable. A like button, a
        # bookmark and a follow control are all checkable, and treating them
        # as switches gave a video feed twelve "toggles" and scored it as a
        # preferences page. Checkable is kept as its own feature and used
        # only where the surrounding shape already rules that out.
        Toggle     = (C 'Toggle') + (C 'Checkbox') + (C 'Radio') + (C 'Switch')
        Checkable  = $checkable
        CoverWeb   = [int]($coverWeb * 1000)     # permille of the viewport
        CoverXc    = [int]($coverXc  * 1000)
        CoverEmb   = [int]($coverEmb * 1000)
        # Opaque nodes whose interior the dump actually carried. Reported, not
        # silently discounted: it is the difference between "I cannot see in"
        # and "I did see in", and the aggregator should know which it got.
        Described  = $described
        # Of the largest opaque region, the share hidden again by native nodes
        # drawn over it. cover_opaque says what ArkUI cannot see; this says how
        # much of that the user cannot see either.
        SurfCovered= [int]($surfCovered * 1000)
        SurfFrac   = if ($vpArea -gt 1) { [int](1000.0 * $surfArea / $vpArea) } else { 0 }
        CoverOpaque= [int]($coverOpaque * 1000)
        IconLeft   = $iconLeft      # small images hugging the left edge
        WideImg    = $wideImg       # images spanning half the width or more
        ImgGeo     = $imgGeo        # images we had a rect for at all
        Editable   = (C 'TextInput') + (C 'TextArea') + (C 'Search') + (C 'RichEditor')
        ListLike   = (C 'List') + (C 'ListItem')
        GridLike   = (C 'Grid') + (C 'GridItem') + (C 'WaterFlow')
        Swiper     = (C 'Swiper') + (C 'Tabs') + (C 'TabContent')
        Scroll     = (C 'Scroll')
        Web        = (C 'Web')
        XComponent = (C 'XComponent')
        Video      = (C 'Video')
        # Counted, because without it the console said "opaque 0" directly
        # above "564 permille of this screen is behind a surface" - the
        # coverage pass recognised these tags and nothing else did.
        Embedded   = (C 'EmbeddedComponent') + (C 'UIExtensionComponent') + (C 'Plugin') +
                     (C 'SurfaceView') + (C 'RichEditor_Surface')
        Canvas     = (C 'Canvas')
        TextLen    = $textLen
        TextMax    = $textMax
        AvgText    = $avgText
        EditPos    = $editPos
        SliderPos  = $sliderPos
        PosSource  = $posSource
        Boxes      = $boxes
        Visible    = $vis.Count
        Overdraw   = if ($vpArea -gt 0) { [math]::Round($areaSum / $vpArea, 2) } else { 0.0 }
        LargestFrac   = if ($vpArea -gt 0) { [int](1000.0 * $bigLeafArea / $vpArea) } else { 0 }
        LargestAspect = if ($bigLeafH -gt 0) { [math]::Round($bigLeafW / $bigLeafH, 2) } else { 0.0 }
        XcName = $xc.Name; XcLib = $xc.Lib; XcType = $xc.XType; XcA11y = $xc.A11y
        XcSecure = $xc.Secure; XcHdr = $xc.Hdr; XcAspect = $xc.Aspect
        XcHint = $xc.Hint; XcConf = $xc.Conf; XcFlags = $xc.Flags; XcWhy = $xc.Why
        FxBlur = $fxBlur; FxShadow = $fxShadow; FxOpacity = $fxOpacity
        FxClip = $fxClip; FxGradient = $fxGrad; FxScore = $fxScore
        DeclRate = $declRate; Lazy = $lazy; Reusable = $reusable; Cached = $cached
    }
}

# Snapshot differencing: the only dynamics available without the collector.
# Sum of absolute per-tag changes, normalised per second. The tally it diffs
# is the VISIBLE one, so scrolling a long page registers as change even though
# the document behind it did not move.
function Get-Churn {
    param($Tally, [double] $Mono)
    if (-not $script:PrevTally) {
        $script:PrevTally = $Tally; $script:PrevMono = $Mono
        return [PSCustomObject]@{ Delta=0; Added=0; Removed=0; Net=0; Shape="NONE"; Rate=0.0; Dt=0.0; First=$true }
    }
    # Direction matters: nodes ARRIVING cost construction, layout and possibly
    # decode. Nodes LEAVING cost teardown, which is far cheaper. A balanced
    # add/remove is a swap (page change, or list recycling); add >> remove is
    # construction, which is the expensive one.
    $added = 0; $removed = 0
    $keys = @($Tally.Keys) + @($script:PrevTally.Keys) | Sort-Object -Unique
    foreach ($k in $keys) {
        $a = if ($Tally.ContainsKey($k)) { [int]$Tally[$k] } else { 0 }
        $b = if ($script:PrevTally.ContainsKey($k)) { [int]$script:PrevTally[$k] } else { 0 }
        if ($a -gt $b) { $added += ($a - $b) } elseif ($b -gt $a) { $removed += ($b - $a) }
    }
    $delta = $added + $removed
    $net   = $added - $removed
    # Shape of the change, which is what maps to cost.
    $shape = "NONE"
    if ($delta -gt 0) {
        $minv = [math]::Min($added, $removed)
        if ($minv -gt 0 -and [math]::Abs($net) -le ($delta * 0.25)) { $shape = "SWAP" }
        elseif ($net -gt 0)  { $shape = "GROW" }
        else                 { $shape = "SHRINK" }
    }
    $dt = $Mono - $script:PrevMono
    if ($dt -le 0) { $dt = 0.001 }
    $script:PrevTally = $Tally; $script:PrevMono = $Mono
    return [PSCustomObject]@{ Delta=$delta; Added=$added; Removed=$removed; Net=$net
                              Shape=$shape; Rate=[math]::Round($delta/$dt,1)
                              Dt=[math]::Round($dt,3); First=$false }
}

# Only the modifiers this script can honestly infer from snapshots. The
# collector emits the rest: WORK_COMMITTED, RENDERER_ANIMATING, GESTURE_ACTIVE.
function Get-Modifiers {
    param($F, $Churn, $Win)
    $mods = @()
    $opaque = $F.Web + $F.XComponent + $F.Embedded
    $mediaish = $F.XComponent + $F.Video
    if (-not $Churn.First) {
        if ($Churn.Delta -eq 0) { $mods += "STATIC" }
        else {
            $mag = if ($Churn.Delta -ge 40) { "HIGH" } else { "LOW" }
            # GROW = construction (dear), SHRINK = teardown (cheap),
            # SWAP = page change or list recycling (dear, but bounded).
            $mods += "CHURN_$($Churn.Shape)_$mag"
        }
    }
    if ($opaque -ge 1 -and $F.Total -lt 60) { $mods += "OPAQUE_DOMINANT" }
    # Not the same thing as OPAQUE_DOMINANT, which is about a thin tree. This
    # is about AREA: most of what the user is looking at is not in this tree,
    # whatever else the tree contains.
    if ($F.CoverOpaque -ge $P.OpaqueCover) { $mods += "SCENE_BEHIND_SURFACE" }
    if ($F.CoverWeb -ge $P.OpaqueCover)    { $mods += "NEEDS_ARKWEB" }
    if ($F.CoverXc  -ge $P.OpaqueCover)    { $mods += "NEEDS_SURFACE_PRODUCER" }
    # The recoverable one: another ArkUI instance, in another process, with a
    # tree of its own. The aggregator closes this hole by asking that process
    # for its record, not by finding a different kind of producer.
    if ($F.CoverEmb -ge $P.OpaqueCover)    { $mods += "NEEDS_PEER_CONTAINER" }
    # The surface is still producing at its own rate; the user is looking at
    # something else over most of it. Two cost centres, both live.
    if ($F.SurfFrac -ge 500 -and $F.SurfCovered -ge $P.SurfaceCovered) { $mods += "SURFACE_BEHIND_UI" }
    if ($mediaish -ge 1) {
        if ($F.Slider -ge 1 -or $F.Button -ge 2) { $mods += "CHROME_VISIBLE" }
        else                                     { $mods += "CHROME_HIDDEN" }
    }
    if ($F.Editable -ge 1) { $mods += "EDITABLE_PRESENT" }
    # More specific than EDITABLE_PRESENT, and worth more to a scheduler: an
    # editable pinned below the content is a composer, which implies an IME
    # window above this one and a caret waking the UI thread with no input.
    if ($F.GeoOK -and $F.Editable -ge 1 -and $F.EditPos -ge $P.EditBotMin) {
        $mods += "COMPOSER_PRESENT"
    }
    # ArkUI's committed layout work is the UI thread: frequency helps, cores do
    # not. Image nodes arriving are the exception - decode may go parallel.
    $mods += "SINGLE_THREAD_LAYOUT"
    if ($F.Image -ge 4 -and $Churn.Added -ge 6) { $mods += "DECODE_LIKELY" }
    if ($F.FxBlur -ge 1)      { $mods += "BLUR_PRESENT" }      # readback + extra pass
    if ($F.FxScore -ge 20)    { $mods += "FX_HEAVY" }
    if ($F.Overdraw -ge 3)    { $mods += "OVERDRAW_HIGH" }
    if ($F.DeclRate -gt 0)    { $mods += "RATE_DECLARED_$($F.DeclRate)" }
    if ($F.Lazy -ge 1)        { $mods += "LAZY_LIST" }
    if ($F.Reusable -ge 1)    { $mods += "REUSE_POOL" }
    if (-not $F.GeoOK)        { $mods += "NO_GEOMETRY" }       # dump carries no rects
    # Say it in the signal, not only on the console: a row classified without
    # the viewport clip is a weaker row and the correlation should know.
    if ($F.GeoOK -and -not $F.VpTrusted) { $mods += "GEO_UNTRUSTED" }
    # No content strings: every text-length test stood down. Counts only.
    if ($F.TextLen -eq 0 -and $F.TextMax -eq 0) { $mods += "NO_TEXT_CONTENT" }
    # A tall page whose off-screen part dwarfs the viewport: virtualisation is
    # NOT doing its job, and every one of those nodes was still laid out.
    if ($F.GeoOK -and $F.OffScreen -ge $F.Visible -and $F.OffScreen -ge 20) { $mods += "OFFSCREEN_HEAVY" }
    if ($Win -and $Win.Name -match $script:SystemWindowPat) { $mods += "SYSTEM_WINDOW" }
    # What sits above the scene changes its cost without changing what it is:
    # an IME shrinks the content and adds a second animating surface, a shade
    # pull composites a blurred panel over everything.
    if ($script:SceneContext) { $mods += $script:SceneContext }
    return $mods
}

# ---------------------------------------------------------------- thresholds

# Every number the classifier uses, in one place, so it can be tuned without
# editing logic - and so `-Fit` can tune it for you from labelled screens.
# scene_thresholds.json beside the script overrides any of these.
$script:P = @{
    IconMin        = 6      # icons that make a grid an icon grid
    GalleryCaption = 10.5   # avg chars: captions on a thumbnail grid, not sentences
    SmallPage      = 150    # a preferences page is far shorter than a content list
    FullBleed      = 980    # permille of panel: a viewfinder, not a player
    SurfaceCovered = 400    # permille of a dominant surface hidden again by the
                            # native nodes drawn over it before the scene stops
                            # being "the surface" and becomes "the UI on top of it"
    OpaqueCover    = 500    # permille of viewport hidden behind surfaces before
                            # the native structure stops describing the scene
    IconShare      = 0.15   # ...or this share of the tree, whichever hits first
    LabelChars     = 18     # avg content length that still reads as a label
    LabelMaxChars  = 40     # longest block that still reads as a label
    SnippetChars   = 25     # avg content length that reads as a snippet
    ReadingChars   = 200    # longest block that reads as an article
    BigItemPermil  = 120    # largest leaf, per-mille, that reads as feed media
    EditTopMax     = 0.33   # editable this high up is a search header
    EditBotMin     = 0.65   # editable this low down is a composer
    ToggleSettings = 3      # switches in a scroller that make it preferences
    SmallTree      = 60     # node count below which a tree is "a small screen"
    ThinTree       = 25     # ...and below which a surface dominates it
    SparseTree     = 20
    TextDominance  = 3.0    # text:image ratio that reads as text-dominant
    MarginStrong   = 2.5    # score margin over the runner-up for "structural"
    MarginWeak     = 1.0    # ...and for "weak"; below this, "low"
    CandMargin     = 2.0    # runner-up within this of the top joins candidates
    ScoreCap       = 10.0   # no class may total more than this. See -Separation:
                            # class ceilings ran from 1.5 to 15.5, so an argmax
                            # over raw totals was not comparing like with like
}

function Import-Thresholds {
    $f = Join-Path $PSScriptRoot "scene_thresholds.json"
    if (-not (Test-Path $f)) { return }
    try { $j = Get-Content $f -Raw | ConvertFrom-Json } catch {
        Write-Host "warning: $f is not valid JSON, using defaults" -ForegroundColor Yellow; return
    }
    foreach ($prop in $j.PSObject.Properties) {
        if ($script:P.ContainsKey($prop.Name)) { $script:P[$prop.Name] = [double]$prop.Value }
    }
    Write-Host "thresholds loaded from $f" -ForegroundColor DarkCyan
}

function Export-Thresholds {
    $f = Join-Path $PSScriptRoot "scene_thresholds.json"
    $o = New-Object PSObject
    foreach ($k in ($script:P.Keys | Sort-Object)) { $o | Add-Member -NotePropertyName $k -NotePropertyValue $script:P[$k] }
    $o | ConvertTo-Json | Set-Content -Path $f -Encoding UTF8
    Write-Host "thresholds written -> $f" -ForegroundColor Green
}

# What each class narrows to, and who can resolve the rest. Kept beside the
# scoring rather than inside it, because the candidate set is a property of the
# class, not of the evidence that found it.
$script:ClassInfo = @{
    MEDIA_PLAYER      = @{ Cand=@("VIDEO");                                Resolve="audio stream usage; surface submit rate" }
    CALL_VIDEO        = @{ Cand=@("VIDEO_CALL");                           Resolve="audio usage confirms VOICE_COMMUNICATION" }
    CAPTURE           = @{ Cand=@("CAMERA");                               Resolve="bound producer on the surface" }
    IMMERSIVE_SURFACE = @{ Cand=@("VIDEO","GAME","MAP","CAMERA");          Resolve="audio usage, surface submit rate, bound producer" }
    WEB_CONTENT       = @{ Cand=@("WEB_ANY");                              Resolve="the ArkWeb record for this window" }
    AUDIO_PLAYER      = @{ Cand=@("MUSIC","PODCAST","AUDIOBOOK");          Resolve="audio stream usage; may continue with the screen off" }
    SPLASH_LOADING    = @{ Cand=@("SPLASH","LOADING");                     Resolve="short-lived; treat as a transition, not a scene" }
    DIALOG            = @{ Cand=@("MODAL","PERMISSION","SHEET","ALERT");   Resolve="window type separates a subwindow from in-page" }
    ICON_PAGER        = @{ Cand=@("LAUNCHER","APP_DRAWER","ONBOARDING");   Resolve="page index separates home pages from the drawer" }
    ICON_GRID         = @{ Cand=@("APP_DRAWER","LAUNCHER_FOLDER","SHORTCUT_GRID"); Resolve="window identity separates the drawer from an app" }
    GALLERY_GRID      = @{ Cand=@("GALLERY","MEDIA_GRID");                 Resolve="-" }
    LIST              = @{ Cand=@("LIST","CONTACTS","INBOX");             Resolve="-" }
    SETTINGS          = @{ Cand=@("SETTINGS","PREFERENCES","ACCOUNT");     Resolve="-" }
    ICON_LIST         = @{ Cand=@("ICON_LIST","CONTACTS","CONVERSATIONS");  Resolve="-" }
    MAP               = @{ Cand=@("MAP","NAVIGATION");                     Resolve="bound producer on the surface" }
    CHAT              = @{ Cand=@("CHAT","COMMENTS","THREAD");            Resolve="IME state separates composing from reading" }
    FORM              = @{ Cand=@("FORM");                                 Resolve="IME state" }
    EDITOR            = @{ Cand=@("DRAWING","PHOTO_EDIT","ANNOTATION");    Resolve="-" }
    MEDIA_VIEW        = @{ Cand=@("PHOTO_VIEW","FULLSCREEN_IMAGE");        Resolve="a pager sibling means a swipeable gallery" }
    FEED              = @{ Cand=@("FEED");                                 Resolve="-" }
    READING           = @{ Cand=@("READING");                              Resolve="-" }
    PAGING            = @{ Cand=@("LAUNCHER","ONBOARDING","GALLERY_SWIPE");Resolve="window identity" }
    SPARSE            = @{ Cand=@();                                       Resolve="check the parse with -Tags" }
    UNCLASSIFIED      = @{ Cand=@();                                       Resolve="-" }
}

# ---------------------------------------------------------------- classify

# Scoring, not first-match. Every class accumulates evidence and the highest
# total wins; the runner-ups become the candidate set and the margin becomes
# the confidence. The old chain returned on the first rule that matched, so a
# loose rule placed early ate screens that a later, better rule would have
# caught - that is what put EDITOR on a launcher and CHAT on Settings. A score
# cannot be shadowed by ordering.
function Get-SceneClass {
    param($F, [string] $WinName = "")

    $P = $script:P
    $S = @{}; $E = @{}
    function Score($cls, $w, $why) {
        if (-not $S.ContainsKey($cls)) { $S[$cls] = 0.0; $E[$cls] = @() }
        $S[$cls] += $w
        if ($why) { $E[$cls] += $why }
    }

    # Embedded too. The modifier pass counted it and the classifier did not,
    # because the two lines differ only in spacing - so every rule gated on
    # "no opaque region" still fired on a window whose content is an
    # EmbeddedComponent, and SETTINGS scored 3.5 on a chat.
    $opaque    = $F.XComponent + $F.Web + $F.Embedded
    $mediaish  = $F.XComponent + $F.Video
    $scrollers = $F.ListLike + $F.GridLike + $F.Scroll + $F.Swiper
    $iconish   = $F.Image + $F.Icon
    $geo       = ($F.PosSource -ne "none")
    # Zero here means the dump carried no content strings, NOT that the text is
    # short. Unknown must never satisfy a test that a value would - the same
    # mistake as treating a zero-sized rect as "off screen".
    $textKnown = ($F.TextLen -gt 0 -or $F.TextMax -gt 0)

    # ---- surfaces ---------------------------------------------------------
    if ($mediaish -ge 1) {
        Score "IMMERSIVE_SURFACE" 2.0 "a surface is present"
        if ($F.Slider -ge 1 -or $F.Progress -ge 1) {
            Score "MEDIA_PLAYER" 5.0 "surface + seekbar"
            if ($F.Button -ge 2) { Score "MEDIA_PLAYER" 1.5 "transport buttons" }
            if ($geo -and $F.SliderPos -ge 0.6) { Score "MEDIA_PLAYER" 1.5 "seekbar low in the frame" }
        }
        if ($F.XComponent -ge 2 -and $F.Slider -eq 0 -and $F.Button -ge 2) {
            Score "CALL_VIDEO" 5.5 "two surfaces + controls, no seekbar"
        }
        if ($F.XComponent -ge 1 -and $F.Button -ge 3 -and $F.Slider -eq 0) {
            Score "CAPTURE" 4.5 "surface + button cluster, no seekbar"
        }
        if ($F.LargestAspect -ge 1.6 -and $F.LargestAspect -le 1.85 -and $F.LargestFrac -ge 400) {
            Score "MEDIA_PLAYER" 1.0 "largest leaf is 16:9 and dominant"
            Score "IMMERSIVE_SURFACE" 0.5 "16:9 dominant leaf"
        }
    }
    # The surface's own declaration, when it said anything. A guess with a
    # stated confidence beats a shrug, and the aggregator can discount it.
    if ($F.XcConf -ge 30) {
        $w = $F.XcConf / 25.0          # 30 -> 1.2, 85 -> 3.4
        switch ($F.XcHint) {
            "VIDEO"     { Score "MEDIA_PLAYER" $w "surface looks like video: $($F.XcWhy)"
                          Score "IMMERSIVE_SURFACE" ($w * 0.4) "surface looks like video" }
            "CAMERA"    { Score "CAPTURE" $w "surface looks like a camera preview: $($F.XcWhy)" }
            "GAME"      { Score "IMMERSIVE_SURFACE" $w "surface looks like a game: $($F.XcWhy)" }
            "MAP"       { Score "IMMERSIVE_SURFACE" ($w * 0.8) "surface looks like a map: $($F.XcWhy)" }
            "CHART"     { Score "EDITOR" ($w * 0.5) "surface looks like a chart" }
            "AUDIO_VIS" { Score "AUDIO_PLAYER" $w "surface looks like an audio visualiser" }
        }
    }
    if ($opaque -ge 1 -and $F.Total -lt $P.ThinTree) {
        Score "IMMERSIVE_SURFACE" 4.0 "surface fills a near-empty tree ($($F.Total) elements)"
    }
    if ($F.Web -ge 1) {
        Score "WEB_CONTENT" 3.0 "a Web component is present"
        if ($F.Total -lt $P.SmallTree + 20) { Score "WEB_CONTENT" 2.0 "and the native tree around it is thin" }
    }
    if ($mediaish -eq 0 -and ($F.Slider -ge 1 -or $F.Progress -ge 1) -and
        $F.Button -ge 2 -and $iconish -ge 1 -and $scrollers -le 1 -and $F.Total -lt 150) {
        Score "AUDIO_PLAYER" 5.0 "transport controls + artwork, no video surface"
    }

    # ---- transient --------------------------------------------------------
    if ($F.Total -lt 40 -and $F.Progress -ge 1 -and $F.Editable -eq 0 -and
        $F.Button -le 1 -and $scrollers -eq 0) {
        Score "SPLASH_LOADING" 4.5 "progress indicator, almost nothing interactive"
    }
    if ($F.Total -lt $P.SmallTree -and $F.Button -ge 2 -and $F.Text -ge 1 -and
        $scrollers -eq 0 -and $F.Editable -le 1 -and $mediaish -eq 0) {
        Score "DIALOG" 4.0 "small tree, buttons and text, nothing scrollable"
    }

    # ---- icon grids -------------------------------------------------------
    # An icon grid is icons arranged ONE PER CELL. That is the test: the cell
    # count tracks the icon count, within a wide band.
    #
    # It used to be gated on icon count matching TEXT count, on the theory that
    # every icon carries one caption. A real launcher broke that at once: 50
    # icons against 401 text nodes, because a launcher window holds far more
    # text than captions - page indicators, folder contents, a search field,
    # widgets. Counting cells rather than captions is more direct, and it
    # survives a dump that carries no content strings at all.
    $manyIcons = ($iconish -ge $P.IconMin -or ($F.Total -gt 0 -and $iconish -ge $F.Total * $P.IconShare))
    # uitest reports a launcher page as a List inside a pager as often as a
    # Grid, so cells is whichever container is actually carrying the items -
    # but ONLY inside a pager. Without that gate a chat thread, which is a
    # plain List of rows with avatars, satisfied "icons across cells" and
    # scored ICON_GRID 5.0 / ICON_PAGER 3.0 against a CHAT that had nothing
    # but its composer. Every vertical list on the device was a candidate
    # launcher. A grid is a grid or it pages; a list is neither.
    $cells = if ($F.GridLike -ge 1) { [math]::Max($F.GridLike, $F.ListLike) }
             elseif ($F.Swiper -ge 1) { $F.ListLike }
             else { 0 }

    if ($WinName -match '(?i)scbdesktop|launcher|home|workspace|negativescreen|appcenter|desktop') {
        if ($iconish -ge 3) {
            Score "ICON_PAGER" 4.0 "launcher window identity"
            Score "ICON_GRID"  2.5 "launcher window identity"
        }
    }
    # Text NODE count, not text content - available even when the dump carries
    # no strings. An icon grid captions roughly every cell; a photo grid does
    # not caption at all, and without this it would read as an icon grid.
    $captioned = ($F.Text -ge ($cells * 0.5))
    if ($cells -ge 4 -and $manyIcons -and $F.Editable -le 1 -and $captioned) {
        $ratio = $iconish / [math]::Max(1.0, [double]$cells)
        if ($ratio -ge 0.25 -and $ratio -le 4.0) {
            Score "ICON_GRID"  4.0 "$iconish icons across $cells grid cells"
            Score "ICON_PAGER" 2.0 "$iconish icons across $cells grid cells"
            # These two had ZERO exclusive evidence between them: every gate
            # that scored one scored the other, cosine 0.75, so which of the
            # pair won was decided by weight bookkeeping rather than by
            # anything on the screen. The pager IS the discriminator, so it
            # has to cut both ways or it is not one.
            if ($F.Swiper -ge 1) {
                Score "ICON_PAGER" 2.5 "and a pager - horizontal pages, not a scroll"
                Score "ICON_GRID" (-2.0) "it pages horizontally - not one scrolling grid"
            } else {
                Score "ICON_GRID" 2.5 "a grid that scrolls vertically, with no pager"
                Score "ICON_PAGER" (-2.0) "no pager - there are no pages to turn"
            }
            # Captions confirm it when the dump carries them. A bonus, never a
            # gate: absent content strings must not read as "not an icon grid".
            if ($textKnown -and $F.AvgText -lt $P.LabelChars) {
                Score "ICON_GRID"  1.0 "captions are label-length (avg $([int]$F.AvgText) chars)"
                Score "ICON_PAGER" 1.0 "captions are label-length"
            }
        }
    }
    # A photo grid: images outnumber any captions, or there are none.
    if ($cells -ge 2 -and $manyIcons -and $iconish -gt ($F.Text * 2)) {
        Score "GALLERY_GRID" 4.5 "grid, images outnumber text $iconish to $($F.Text)"
    }
    if ($cells -ge 2 -and $F.LargestFrac -ge $P.BigItemPermil -and $F.Text -le 2) {
        Score "GALLERY_GRID" 1.5 "large unlabelled grid cells"
    }

    # ---- lists, chats, forms ----------------------------------------------
    if ($F.Toggle -ge $P.ToggleSettings -and $scrollers -ge 1) {
        Score "SETTINGS" 4.5 "$($F.Toggle) switches in a scroller"
    }
    # A composer - an editable pinned below the content - is the one feature
    # that separates a conversation from the list of conversations. Both are
    # a scroller of rows with a small image on the left, so ICON_LIST would
    # otherwise out-score CHAT on exactly that screen. The split is a cost
    # difference before it is a taxonomy: a composer means a focused editable,
    # which means an IME window composited above this one, a caret animating
    # with nobody touching the screen, a viewport that resizes when the
    # keyboard opens, and content that appends at the tail with no input at
    # all. An icon list is still until the user moves it.
    $composer = ($F.Editable -ge 1 -and $geo -and $F.EditPos -ge $P.EditBotMin)
    if ($F.Editable -ge 1 -and $scrollers -ge 1 -and $geo) {
        if ($F.EditPos -ge 0 -and $F.EditPos -le $P.EditTopMax) {
            Score "LIST" 3.5 "editable at $([int]($F.EditPos*100))% down - a search header, not a composer"
        }
        if ($composer) {
            Score "CHAT" 4.5 "editable at $([int]($F.EditPos*100))% down - a composer below the content"
            # Avatars on the left are what a chat SHARES with an icon list, so
            # on their own they are evidence for neither. Beside a composer
            # they are evidence for this one.
            if ($F.IconLeft -ge 2) {
                Score "CHAT" 1.0 "$($F.IconLeft) small images on the left edge - avatars above a composer"
            }
            Score "ICON_LIST" (-2.5) "these rows sit above a composer - a conversation, not the list of them"
            Score "FEED"      (-2.0) "these rows sit above a composer"
            Score "LIST"      (-1.5) "these rows sit above a composer"
        }
    }
    if ($F.Editable -ge 1 -and $scrollers -ge 1 -and $F.Text -ge 8 -and $textKnown -and
        ($F.TextMax -ge $P.LabelMaxChars -or $F.AvgText -ge $P.SnippetChars)) {
        Score "CHAT" 2.0 "message-length text beside an input"
    }
    if ($F.Toggle -ge $P.ToggleSettings) { Score "CHAT" (-3.0) "switches do not belong in a conversation" }
    if ($F.Editable -ge 2 -and $scrollers -le 1) {
        Score "FORM" 4.0 "$($F.Editable) editables, little scrolling"
    }

    # ---- canvas, single image ---------------------------------------------
    if ($F.Canvas -ge 1) {
        Score "EDITOR" 1.5 "a canvas is present"
        if ($iconish -le 3 -and $scrollers -le 1 -and $F.Total -lt 200 -and
            ($F.Button -ge 3 -or $F.Slider -ge 1)) {
            Score "EDITOR" 3.5 "canvas is the content, with a tool palette"
        }
    }
    if ($iconish -ge 1 -and $iconish -le 3 -and $F.Total -lt $P.SmallTree -and
        $F.Text -le 4 -and $F.Editable -eq 0 -and $mediaish -eq 0) {
        Score "MEDIA_VIEW" 4.0 "single dominant image, minimal chrome"
        # The rule said "dominant" and never measured it: counts alone cannot
        # tell one fullscreen photo from one small image on a sparse screen,
        # and this was a one-gate class that could not score above 4.0 no
        # matter what it saw. Say it with the rect when there is one.
        if ($geo -and $F.LargestFrac -ge 500) {
            Score "MEDIA_VIEW" 3.0 "and the image really does fill the frame ($($F.LargestFrac)/1000)"
        }
        if ($geo -and $F.LargestFrac -lt 200) {
            Score "MEDIA_VIEW" (-2.5) "the largest leaf is small - a sparse screen, not a photo"
        }
    }

    # ---- shapes that only a labelled corpus could have shown ---------------
    #
    # Everything below was added after 46 labelled screens scored 20%. The
    # single largest cause was not a threshold: SETTINGS and MAP were never
    # scored by any rule at all - they existed only as candidate names - so a
    # quarter of the corpus was unwinnable by construction. -Fit now refuses
    # to let that happen silently again.
    #
    # These rules are fitted to one device, one source and 46 screens. Treat
    # the two-sample classes as provisional: they are a hypothesis written
    # down, not a result.

    # Checkable rows, but only where nothing else on screen explains them: no
    # pager, no surface, few images. A like button is checkable too, so the
    # gate does the work the attribute cannot.
    if ($F.Checkable -ge 3 -and $scrollers -ge 1 -and $F.Swiper -eq 0 -and
        $opaque -eq 0 -and $F.Image -le 8) {
        Score "SETTINGS" 4.0 "$($F.Checkable) checkable rows, no pager and no media"
    }

    # A preferences page is a SHORT scrolled list of labelled rows with no
    # media. The intended signal was the switch count, and uitest reports no
    # Toggle at all, so the shape has to come from size and composition.
    if ($scrollers -ge 1 -and $F.Total -lt $P.SmallPage -and $iconish -ge 1 -and
        $mediaish -eq 0 -and $opaque -eq 0) {
        Score "SETTINGS" 3.5 "a short scrolled list of labelled rows, no media ($($F.Total) nodes)"
        if ($textKnown -and $F.AvgText -ge $P.LabelChars) {
            Score "SETTINGS" 1.5 "row labels run to sentence length (avg $([int]$F.AvgText))"
        }
    }
    # ...and a content list is long. Size is the separator in this corpus;
    # the ratios overlap almost exactly.
    if ($scrollers -ge 1 -and $F.Total -ge $P.SmallPage -and $F.ListLike -ge 8) {
        Score "LIST" 1.5 "a long list ($($F.ListLike) rows in $($F.Total) nodes)"
    }

    # A thumbnail grid is not a Grid in this vocabulary - it comes through as
    # a List inside a pager. Images per cell survives captions; images
    # outnumbering text 2:1 does not, because every thumbnail has a title.
    $galleryCells = [math]::Max($F.GridLike, $F.ListLike)
    if ($galleryCells -ge 1 -and $F.Image -ge 6 -and $opaque -eq 0 -and $F.Swiper -ge 3 -and
        $textKnown -and $F.AvgText -lt $P.GalleryCaption) {
        Score "GALLERY_GRID" 4.5 "$($F.Image) thumbnails in a pager, captions not sentences (avg $([int]$F.AvgText))"
    }

    # Two opaque regions is a web view with its own media inside it. One, with
    # almost no native text around it, is an article being read THROUGH a web
    # view - the same component, a different scene.
    if ($opaque -ge 2) { Score "WEB_CONTENT" 3.0 "two opaque regions - a web view with media in it" }
    if ($F.Web -ge 1 -and $F.Text -le 2 -and $opaque -le 1) {
        Score "READING" 3.5 "one web view and almost no native text - an article, not a page of controls"
    }
    # Images are what separate the two in this corpus: an article has almost
    # none of its own, a web page is full of them.
    if ($F.Web -ge 1 -and $F.Image -le 3 -and $F.ListLike -le 7 -and $F.GridLike -eq 0) {
        Score "READING"     3.0 "a web view carrying text and almost no images"
        Score "WEB_CONTENT" (-1.5) "almost no images for a web page"
    }

    # Video in a feed plays without visible transport: a dominant surface
    # inside a pager is a player even with no seekbar on screen.
    if ($opaque -ge 1 -and $F.LargestFrac -ge 850 -and $F.Swiper -ge 1 -and $F.GridLike -eq 0) {
        Score "MEDIA_PLAYER" 3.0 "a dominant surface inside a pager - a video feed"
    }

    # A viewfinder fills the panel completely and has no transport controls.
    # A player does not fill it and has a seekbar; a map fills most of it and
    # has neither.
    if ($opaque -ge 1 -and $F.LargestFrac -ge $P.FullBleed -and $F.Swiper -eq 0 -and
        $F.Slider -eq 0 -and $F.ListLike -ge 4) {
        Score "CAPTURE" 5.0 "a full-bleed surface with a control strip and no transport"
    }
    if ($opaque -ge 1 -and $F.ListLike -le 2 -and $F.Swiper -ge 2 -and $F.Slider -eq 0 -and
        $F.LargestFrac -ge 900 -and $F.LargestFrac -lt $P.FullBleed) {
        Score "MAP" 4.0 "a near-full surface, no list and no transport controls"
    }
    if ($opaque -ge 1 -and $F.ListLike -eq 0 -and $F.GridLike -eq 0 -and $F.Swiper -eq 0 -and
        $F.Total -lt 100) {
        Score "IMMERSIVE_SURFACE" 3.0 "a surface with no containers around it at all"
    }

    # Rows wrapped around a surface are chrome, not content. Without this a
    # viewfinder with a control strip outscores CAPTURE as a list, which it
    # did four times out of four.
    if ($opaque -ge 1 -and $F.LargestFrac -ge 900) {
        Score "LIST" (-2.0) "a surface dominates the frame - the rows are chrome"
    }

    # ---- three kinds of row -----------------------------------------------
    #
    # All three are a scroller full of rows, and their node counts barely
    # differ. What differs is where the pixels are, which is a cost
    # difference before it is a taxonomy:
    #
    #   LIST       text only. No decode per row at all.
    #   ICON_LIST  a small image hugging the left edge of each row. One small
    #              decode per row, and the row height is text-driven.
    #   FEED       an image spanning the row with text around or below it.
    #              A full-width decode per card, and the item is tall.
    if ($scrollers -ge 1 -and $F.ListLike -ge 3 -and $geo) {
        $rows_ = [double][math]::Max(1, $F.ListLike)
        if ($F.IconLeft -ge ($rows_ * 0.5) -and $F.WideImg -le ($rows_ * 0.25)) {
            Score "ICON_LIST" 4.5 "$($F.IconLeft) small images on the left edge across $($F.ListLike) rows"
            if ($textKnown -and $F.AvgText -lt $P.SnippetChars) {
                Score "ICON_LIST" 1.0 "label-length text beside them (avg $([int]$F.AvgText))"
            }
        }
        if ($F.WideImg -ge ($rows_ * 0.4)) {
            Score "FEED" 4.0 "$($F.WideImg) images spanning the row - cards, not rows"
        }
        if ($iconish -le ($rows_ * 0.2)) {
            Score "LIST" 2.0 "rows carry text and almost no images"
        }
    }

    # ---- feed vs list ------------------------------------------------------
    # Both have icons and labels. What separates a feed is LARGE items and
    # SNIPPETS; a list has small icons and short labels. Counts cannot tell
    # them apart, these two tests can.
    if ($scrollers -ge 1) {
        Score "LIST" 0.5 "a scroller is present"
        if ($iconish -ge 4 -and $F.Text -ge 4) {
            if ($F.LargestFrac -ge $P.BigItemPermil) { Score "FEED" 3.0 "large media items ($($F.LargestFrac)/1000)" }
            if ($textKnown -and $F.AvgText -ge $P.SnippetChars) { Score "FEED" 3.0 "snippet-length text (avg $([int]$F.AvgText) chars)" }
            if ($textKnown -and $F.AvgText -lt $P.LabelChars)    { Score "LIST" 1.0 "short labels (avg $([int]$F.AvgText) chars)" }
        }
        if ($F.ListLike -ge 2) { Score "LIST" 1.0 "List/ListItem structure" }
    }
    # A grid of cells is not an article, whatever the text:image ratio says.
    if ($F.Text -gt 0 -and $F.Text -ge ($iconish * $P.TextDominance) -and $F.GridLike -lt 4) {
        Score "READING" 2.0 "text dominant over images"
        if ($F.TextMax -ge $P.ReadingChars) { Score "READING" 3.0 "a long contiguous block ($($F.TextMax) chars)" }
    }
    if ($F.Swiper -ge 1 -and $F.Total -lt 120) {
        Score "PAGING" 1.5 "a pager with a small tree"
    }
    if ($F.Total -lt $P.SparseTree) {
        Score "SPARSE" 1.5 "very few elements ($($F.Total))"
    }

    # ---- resolve -----------------------------------------------------------
    #
    # Classes do not have comparable ceilings. Before the cap, the most
    # IMMERSIVE_SURFACE could ever score was 15.5 and the most MAP could ever
    # score was 4.0 - not because a map is less certain, but because more
    # rules happened to be written for surfaces. An argmax over totals on that
    # scale is not comparing like with like: a class accumulates its way past
    # a better answer that has nowhere left to climb. Capping bounds the
    # runaway classes without changing the ordering inside any one of them.
    # `-Separation` prints every ceiling and names the classes still below it.
    foreach ($k in @($S.Keys)) {
        if ($S[$k] -gt $P.ScoreCap) { $S[$k] = $P.ScoreCap }
    }
    if ($S.Count -eq 0) {
        return @{ Class="UNCLASSIFIED"; Cand=@(); Conf="none"; Score=0.0; Margin=0.0
                  Ev=@("no evidence matched"); Resolve="-"; Ranked="" }
    }
    $ranked = @($S.GetEnumerator() | Sort-Object Value -Descending)
    $top    = $ranked[0]
    # Negative evidence pushes a class below zero; it must not inflate the
    # margin of the winner, which is supposed to mean "how much better than
    # the next PLAUSIBLE answer".
    $second = if ($ranked.Count -gt 1) { [math]::Max(0.0, $ranked[1].Value) } else { 0.0 }
    $margin = $top.Value - $second

    $conf = if ($margin -ge $P.MarginStrong) { "structural" }
            elseif ($margin -ge $P.MarginWeak) { "weak" }
            else { "low" }

    # Candidates: the class itself plus anything the evidence could not rule
    # out. A narrow set that is wrong is worse than a wide set that is honest.
    $cand = @()
    if ($script:ClassInfo.ContainsKey($top.Key)) { $cand += $script:ClassInfo[$top.Key].Cand }
    foreach ($r in $ranked) {
        if ($r.Key -eq $top.Key) { continue }
        if (($top.Value - $r.Value) -le $P.CandMargin -and $r.Value -gt 0) { $cand += $r.Key }
    }
    $cand = @($cand | Select-Object -Unique)

    $rankStr = (($ranked | Select-Object -First 4 | ForEach-Object { "$($_.Key):$([math]::Round($_.Value,1))" }) -join " ")
    $resolve = if ($script:ClassInfo.ContainsKey($top.Key)) { $script:ClassInfo[$top.Key].Resolve } else { "-" }

    return @{ Class=$top.Key; Cand=$cand; Conf=$conf; All=$S; AllEv=$E
              Score=[math]::Round($top.Value,1); Margin=[math]::Round($margin,1)
              Ev=$E[$top.Key]; Resolve=$resolve; Ranked=$rankStr }
}


# ---------------------------------------------------------------- deep probe
#
# The ArkUI tree says a surface EXISTS. It cannot say what is behind it, and
# never will - that is app-owned content. But the process that owns it is wide
# open over hdc, and a process cannot hide what it loaded, what it named its
# threads, or which device nodes it opened.
#
# Ranked by how hard they are to fake or mistake:
#
#   device nodes   /dev/vcodec, /dev/video, /dev/dri - a hardware decoder is
#                  open or it is not. As close to proof as this gets.
#   thread names   decoders, camera streams and game engines all name their
#                  threads, and nobody renames them to mislead a profiler.
#   libraries      a process that mapped libavplayer is doing playback.
#                  Weak for libEGL/libGLES, which ArkUI itself loads.
#   surface names  the RS node name often carries the XComponent's own id.
#
# This is identification, not instrumentation: every one of these is a read.
# ---- process evidence ---------------------------------------------------
#
# This used to end in a weighted verdict - VIDEO 95% and so on - and the
# verdict was wrong often enough to be worse than nothing, for a reason no
# amount of tuning fixes: the evidence is PROCESS-scoped and the question is
# NODE-scoped. A browser has web threads on a settings page. A super-app maps
# a decoder library on its launcher. A game engine's render thread is alive
# while the pause menu is up. None of that says what is behind the hole on
# screen right now, and a confident number attached to it reads as if it did.
#
# So this prints facts and stops. Threads, mapped libraries and open device
# nodes are real and sometimes decisive - /dev/video0 open means a camera is
# streaming somewhere in this process - but the attribution is yours to make,
# with the opaque-node records from -Surfaces beside them.
function Invoke-DeepProbe {
    param([int] $Id, [string] $ProcId, [string] $WinName)

    Write-Host ""
    Write-Host ("PROCESS EVIDENCE  window {0}  pid {1}  ({2})" -f $Id, $ProcId, $WinName) -ForegroundColor Cyan
    Write-Host  "  scope: the whole process, NOT any one node on screen." -ForegroundColor DarkYellow
    if (-not $ProcId -or $ProcId -notmatch '^\d+$') {
        Write-Host "  no pid for this window - pass -WindowId for an app window" -ForegroundColor Yellow
        return
    }

    # Thread names. The most identifying thing available for free: a decoder
    # names its threads, so does a camera pipeline, so does every game engine.
    $comms = @(Invoke-HdcRaw "shell `"cat /proc/$ProcId/task/*/comm`"" |
               Where-Object { $_ -and $_ -notmatch 'No such|Permission' } |
               ForEach-Object { $_.Trim() })
    $threadTotal = $comms.Count

    $libs = @(Invoke-HdcRaw "shell `"grep -o 'lib[A-Za-z0-9_.+-]*\.so' /proc/$ProcId/maps | sort -u`"" |
              Where-Object { $_ -and $_ -notmatch 'No such|Permission|not found' } |
              ForEach-Object { $_.Trim() })

    # Open device nodes are the strongest of the three, because a file
    # descriptor on /dev/vcodec is a decoder that is open NOW, not a library
    # that was linked at startup.
    $devs = @(Invoke-HdcRaw "shell `"ls -l /proc/$ProcId/fd`"" |
              ForEach-Object { $m = [regex]::Match($_, '(/dev/[A-Za-z0-9_/\.\-]+)'); if ($m.Success) { $m.Value } } |
              Select-Object -Unique)

    $shown = @($comms | Where-Object { $_ -notmatch '(?i)^(ipc|os_|hap|jit|gc_|binder|pool|worker)' } |
                        Select-Object -Unique -First 14)
    Write-Host ("  threads    {0}" -f ($shown -join ", "))
    Write-Host ("             {0} total, {1} shown after dropping runtime threads" -f $threadTotal, $shown.Count) -ForegroundColor DarkGray

    if ($libs.Count -gt 0) {
        $interesting = @($libs | Where-Object { $_ -match '(?i)codec|media|player|camera|unity|cocos|vulkan|ffmpeg|map|web_engine|nweb|hevc|h264|gles|egl' } |
                                 Select-Object -First 12)
        if ($interesting.Count -gt 0) { Write-Host ("  libraries  {0}" -f ($interesting -join ", ")) }
        else { Write-Host ("  libraries  {0} mapped, none media- or render-related" -f $libs.Count) -ForegroundColor DarkGray }
    } else {
        Write-Host "  libraries  unreadable (/proc/<pid>/maps needs root here)" -ForegroundColor DarkGray
    }

    $devShown = @($devs | Where-Object { $_ -notmatch '(?i)/dev/(null|zero|urandom|random|ashmem|binder|hwbinder|console|ptmx)$' } |
                          Select-Object -First 10)
    if ($devShown.Count -gt 0) { Write-Host ("  devices    {0}" -f ($devShown -join ", ")) }
    else { Write-Host "  devices    none open beyond the usual runtime nodes" -ForegroundColor DarkGray }

    Write-Host "  no verdict is printed: this evidence cannot be attributed to a node." -ForegroundColor DarkGray
}

# What this build exposes. Run once; it tells you which identification
# channels exist here, and the list is short enough to read.
function Show-Services {
    Write-Host ""
    Write-Host "services this build exposes to hidumper:" -ForegroundColor Cyan
    $svc = Invoke-HdcRaw "shell `"hidumper -ls`""
    $interesting = @()
    foreach ($l in $svc) {
        $t = $l.Trim()
        if (-not $t) { continue }
        Write-Host ("  {0}" -f $t)
        if ($t -match '(?i)render|media|camera|audio|player|graphic|display|power|thermal') { $interesting += $t }
    }
    if ($interesting.Count -gt 0) {
        Write-Host ""
        Write-Host "worth probing for surface identity:" -ForegroundColor Green
        foreach ($i in $interesting) { Write-Host ("  {0}" -f $i) }
    }
}

# ---------------------------------------------------------------- calibration
#
# The loop this exists for:
#
#   1. put a screen up, run with -Label <WHAT IT ACTUALLY IS>
#   2. repeat for a dozen or two screens
#   3. run -Fit, OFFLINE, with no device attached
#
# -Fit re-scores every labelled row through the live classifier, so tuning is
# a second per pass instead of a trip to the phone. Nothing leaves the box.

function Convert-RowToFeatures {
    param($Row)
    function N($v) { if ($null -eq $v -or $v -eq "") { 0.0 } else { [double]$v } }
    [PSCustomObject]@{
        Total = N $Row.total; Text = N $Row.text; Image = N $Row.image; Icon = N $Row.icon
        Button = N $Row.button; Slider = N $Row.slider; Progress = N $Row.progress
        Toggle = N $Row.toggle; Checkable = N $Row.checkable; Editable = N $Row.editable
        IconLeft = N $Row.icon_left; WideImg = N $Row.wide_img; ImgGeo = N $Row.img_geo
        CoverOpaque = N $Row.cover_opaque; CoverWeb = N $Row.cover_web; CoverXc = N $Row.cover_xc
        CoverEmb = N $Row.cover_emb; Embedded = N $Row.embedded; Described = N $Row.described
        SurfFrac = N $Row.surf_frac; SurfCovered = N $Row.surf_covered
        ListLike = N $Row.listlike; GridLike = N $Row.gridlike
        Swiper = N $Row.swiper; Scroll = N $Row.scroll
        Web = N $Row.web; XComponent = N $Row.xcomponent; Video = N $Row.video
        Canvas = N $Row.canvas
        TextLen = N $Row.textlen; TextMax = N $Row.textmax; AvgText = N $Row.avgtext
        EditPos = N $Row.edit_pos; SliderPos = N $Row.slider_pos
        PosSource = if ($Row.pos_source) { [string]$Row.pos_source } else { "none" }
        LargestFrac = N $Row.largest_frac; LargestAspect = N $Row.largest_aspect
        # the opaque hint feeds the scorer, so -Fit must replay it too or
        # offline accuracy would not match what the device produced
        XcHint = if ($Row.xc_hint) { [string]$Row.xc_hint } else { "NONE" }
        XcConf = N $Row.xc_conf
        XcWhy  = if ($Row.xc_flags) { [string]$Row.xc_flags } else { "" }
    }
}

function Measure-Fit {
    param($Rows)
    $ok = 0; $wrong = @()
    # Every class any rule managed to score, anywhere in the corpus. A label
    # that never appears here cannot be produced by the classifier at all, and
    # no amount of threshold sweeping will reach it - which is a different
    # failure from a rule being wrong, and it hid ten Settings screens and two
    # maps behind a plausible-looking 20%.
    $reachable = @{}
    foreach ($r in $Rows) {
        $f = Convert-RowToFeatures $r
        $res = Get-SceneClass -F $f -WinName ([string]$r.name)
        foreach ($k in $res.All.Keys) { $reachable[$k] = $true }
        if ($res.Class -eq $r.truth) { $ok++ }
        else { $wrong += [PSCustomObject]@{ Truth=$r.truth; Got=$res.Class; Ranked=$res.Ranked; Name=$r.name } }
    }
    return [PSCustomObject]@{ Ok=$ok; Total=$Rows.Count; Wrong=$wrong; Reachable=$reachable }
}

function Invoke-Fit {
    param([string] $Path, [switch] $Apply)

    if (-not (Test-Path $Path)) {
        Write-Host "no calibration file at $Path" -ForegroundColor Red
        Write-Host "label some screens first:  scene_class.cmd -Label LIST" -ForegroundColor Yellow
        return
    }
    $rows = @(Import-Csv $Path | Where-Object { $_.truth -and $_.truth -ne "" })
    if ($rows.Count -lt 2) { Write-Host "need at least two labelled rows" -ForegroundColor Red; return }

    Write-Host ""
    Write-Host "$($rows.Count) labelled screens from $Path"
    $byClass = $rows | Group-Object truth | Sort-Object Count -Descending
    Write-Host ("  " + (($byClass | ForEach-Object { "$($_.Name) x$($_.Count)" }) -join ", "))

    # Rows whose screen was mostly inside a surface. Their label is about
    # pixels the features never contained, so counting them in the accuracy
    # figure measures the labeller, not the classifier.
    $blind = @($rows | Where-Object { $_.cover_opaque -and [int]$_.cover_opaque -ge 500 })
    if ($blind.Count -gt 0) {
        Write-Host ""
        Write-Host ("  {0} of {1} rows are mostly behind a surface (cover_opaque >= 500)." -f $blind.Count, $rows.Count) -ForegroundColor Yellow
        $bc = @($blind | Group-Object truth | Sort-Object Count -Descending |
                ForEach-Object { "$($_.Name) x$($_.Count)" })
        Write-Host ("    {0}" -f ($bc -join ", ")) -ForegroundColor DarkGray
        Write-Host  "    ArkUI could not see what you labelled there. Scored separately below." -ForegroundColor DarkGray
        $rows = @($rows | Where-Object { -not $_.cover_opaque -or [int]$_.cover_opaque -lt 500 })
        if ($rows.Count -lt 2) {
            Write-Host "  nothing left to fit once those are set aside." -ForegroundColor Red
            return
        }
    }

    $base = Measure-Fit $rows
    Write-Host ""
    Write-Host ("baseline  {0}/{1} correct ({2}%)" -f $base.Ok, $base.Total, [int](100.0*$base.Ok/$base.Total)) -ForegroundColor Cyan

    # Unreachable labels first: a percentage computed over rows that cannot be
    # won reads as a tuning problem when it is a missing rule.
    $groups = @($rows | Group-Object truth | Sort-Object Count -Descending)
    $unreachable = @(); $lost = 0
    foreach ($g in $groups) {
        if (-not $base.Reachable.ContainsKey($g.Name)) { $unreachable += ("{0} x{1}" -f $g.Name, $g.Count); $lost += $g.Count }
    }
    if ($unreachable.Count -gt 0) {
        Write-Host ""
        Write-Host ("  NO RULE SCORES: {0}" -f ($unreachable -join ", ")) -ForegroundColor Red
        Write-Host ("  {0} of {1} rows ({2}%) cannot be classified correctly by any threshold." -f `
                    $lost, $base.Total, [int](100.0*$lost/$base.Total)) -ForegroundColor Red
        Write-Host  "  These need a rule written for them, not a number moved." -ForegroundColor Red
    }
    foreach ($w in $base.Wrong) {
        Write-Host ("  {0,-16} -> {1,-16}  {2}" -f $w.Truth, $w.Got, $w.Ranked) -ForegroundColor Yellow
    }

    # Coordinate descent, one threshold at a time. Deliberately not a general
    # optimiser: a single changed number with a stated effect is reviewable,
    # and a classifier nobody can read is worse than one that is a bit wrong.
    Write-Host ""
    Write-Host "sweeping thresholds..."
    $factors = @(0.5, 0.67, 0.8, 1.25, 1.5, 2.0)
    $bestOk = $base.Ok; $bestKey = $null; $bestVal = $null
    foreach ($k in @($script:P.Keys)) {
        if ($k -like "Margin*" -or $k -eq "CandMargin") { continue }   # confidence, not class
        $orig = $script:P[$k]
        foreach ($fct in $factors) {
            $try = $orig * $fct
            if ($k -like "Edit*" -or $k -eq "IconShare") { if ($try -gt 1.0) { continue } }
            if ($try -le 0) { continue }
            $script:P[$k] = $try
            $m = Measure-Fit $rows
            if ($m.Ok -gt $bestOk) { $bestOk = $m.Ok; $bestKey = $k; $bestVal = $try }
        }
        $script:P[$k] = $orig
    }

    if (-not $bestKey) {
        Write-Host "no single threshold change improves on the baseline." -ForegroundColor DarkYellow
        Write-Host "the remaining errors need a rule, not a number - the ranked scores above say which." -ForegroundColor DarkYellow
    } else {
        Write-Host ("best single change: {0}  {1} -> {2}   gives {3}/{4} ({5}%)" -f `
                    $bestKey, $script:P[$bestKey], [math]::Round($bestVal,3), $bestOk, $base.Total,
                    [int](100.0*$bestOk/$base.Total)) -ForegroundColor Green
        if ($Apply) {
            $script:P[$bestKey] = $bestVal
            Export-Thresholds
            Write-Host "re-run -Fit -Apply to take the next step." -ForegroundColor Green
        } else {
            Write-Host "re-run with -Apply to write it to scene_thresholds.json." -ForegroundColor DarkCyan
        }
    }

    # Per-class feature ranges. This is what tells you WHICH feature could
    # separate two classes that keep being confused.
    Write-Host ""
    if ($blind.Count -gt 0) {
        $bm = Measure-Fit $blind
        Write-Host ""
        Write-Host ("behind-surface rows, scored separately: {0}/{1} ({2}%)" -f `
                    $bm.Ok, $bm.Total, [int](100.0*$bm.Ok/$bm.Total)) -ForegroundColor DarkYellow
        Write-Host  "  a high number here is not reassurance - it means the chrome happened to" -ForegroundColor DarkGray
        Write-Host  "  agree with what was behind it. Only a surface producer settles these." -ForegroundColor DarkGray
    }

    Write-Host "feature ranges by labelled class:"
    # image, button and the opaque count belong here: GALLERY_GRID and
    # MEDIA_PLAYER are separated by them and by nothing else in this table,
    # and a range table that omits the deciding feature sends you tuning the
    # wrong number.
    $cols = @("total","text","image","icon","button","toggle","slider","editable","gridlike","listlike","swiper","opaque","avgtext","textmax")
    Write-Host ("  {0,-14} {1}" -f "class", (($cols | ForEach-Object { "{0,9}" -f $_ }) -join ""))
    foreach ($g in $byClass) {
        $cells = foreach ($c in $cols) {
            $vals = @($g.Group | ForEach-Object { $_.$c } | Where-Object { $_ -ne "" } | ForEach-Object { [double]$_ })
            if ($vals.Count -eq 0) { "{0,9}" -f "-" }
            else {
                $lo = [math]::Round(($vals | Measure-Object -Minimum).Minimum,1)
                $hi = [math]::Round(($vals | Measure-Object -Maximum).Maximum,1)
                if ($lo -eq $hi) { "{0,9}" -f $lo } else { "{0,9}" -f "$lo-$hi" }
            }
        }
        Write-Host ("  {0,-14} {1}" -f $g.Name, ($cells -join ""))
    }
}

# ---------------------------------------------------------------- one pass

function Invoke-Classify {
    param([int] $Id, [object[]] $Table, [switch] $Terse)

    $mono = Get-DeviceMono
    $win  = $Table | Where-Object { $_.WinId -eq $Id } | Select-Object -First 1
    $name = if ($win) { $win.Name } else { "?" }

    Set-WinRect -Row $win
    $lines = Get-Tree -Id $Id
    if (-not $lines -or $lines.Count -lt 2) {
        Write-Host "empty tree for window $Id - app restarted after param set?" -ForegroundColor Yellow
        return $null
    }
    $F     = Get-Features -Lines $lines
    $churn = Get-Churn -Tally $F.Tally -Mono $mono
    $R     = Get-SceneClass -F $F -WinName $name
    $mods  = Get-Modifiers -F $F -Churn $churn -Win $win
    $script:LastScores = $R.All

    if ($R.Class -eq $script:PrevClass) { $script:StableTicks++ } else { $script:StableTicks = 0 }
    $script:PrevClass = $R.Class

    $colour = switch ($R.Conf) { "structural" {"Green"} "weak" {"Yellow"} default {"DarkYellow"} }
    $scrollers = $F.ListLike + $F.GridLike + $F.Scroll + $F.Swiper

    if ($Terse) {
        Write-Host ("{0,11:F3}  {1,-18} {2,-44} {3,-10} {4}" -f `
                    $mono, $R.Class, ($mods -join "|"), "+$($churn.Added)/-$($churn.Removed)", $name) -ForegroundColor $colour
    } else {
        Write-Host ""
        Write-Host ("window {0} ({1})   device mono {2:F3}s" -f $Id, $name, $mono)
        Write-Host ("scene        {0}" -f $R.Class) -ForegroundColor $colour
        if ($R.Cand.Count -gt 0) { Write-Host ("  candidates {0}" -f ($R.Cand -join ", ")) }
        Write-Host ("  confidence {0}  (score {1}, margin {2} over the runner-up)" -f $R.Conf, $R.Score, $R.Margin)
        Write-Host ("  ranked     {0}" -f $R.Ranked)
        Write-Host ("  modifiers  {0}" -f ($mods -join " | "))
        Write-Host ("  evidence   {0}" -f ($R.Ev -join "; "))
        if ($R.Resolve -ne "-") { Write-Host ("  resolve by {0}" -f $R.Resolve) -ForegroundColor DarkCyan }
        Write-Host ""
        Write-Host "  STRUCTURE  total $($F.Total)  text $($F.Text)  image $($F.Image)  button $($F.Button)"
        Write-Host "             slider $($F.Slider)  editable $($F.Editable)  scrollers $scrollers"
        Write-Host "             opaque $($F.Web + $F.XComponent + $F.Embedded) (Web $($F.Web), XComponent $($F.XComponent), embedded $($F.Embedded))"
        if ($F.Described -ge 1) {
            Write-Host "             $($F.Described) of them carry their own subtree in this dump - described, not hidden" -ForegroundColor DarkCyan
        }
        if ($F.SurfFrac -ge 1) {
            Write-Host ("             largest surface covers {0}/1000 of the viewport, and {1}/1000 of IT is behind native nodes" -f $F.SurfFrac, $F.SurfCovered)
        }
        if ($F.XcHint -ne "NONE") {
            Write-Host ("  OPAQUE     hint {0} ({1}% confident)  type {2}  aspect {3}" -f $F.XcHint, $F.XcConf, $F.XcType, $F.XcAspect)
            if ($F.XcName -or $F.XcLib) { Write-Host ("             id '{0}'  library '{1}'" -f $F.XcName, $F.XcLib) }
            if ($F.XcA11y)  { Write-Host ("             accessibility '{0}'" -f $F.XcA11y) }
            if ($F.XcWhy)   { Write-Host ("             {0}" -f $F.XcWhy) -ForegroundColor DarkCyan }
            Write-Host     ("             the buffer queue settles this; join on the surface id") -ForegroundColor DarkGray
        }
        Write-Host "             textlen $($F.TextLen)  longest $($F.TextMax)  avg/node $($F.AvgText)"
        Write-Host "  POSITION   editable $($F.EditPos)  slider $($F.SliderPos)  (source: $($F.PosSource))"
        Write-Host "  PARSE      $($F.ParseMode), $($F.Nodes) nodes, $($F.Boxes) with rects"
        # One readable verdict instead of a wall of numbers. Everything the
        # script needed to decide is on these two lines.
        $clip = if ($F.VpTrusted) { "ON" } elseif ($F.ClipAborted) { "ABORTED" } elseif (-not $F.GeoOK) { "off (no rects)" } else { "off (viewport unconfirmed)" }
        Write-Host "  VIEWPORT   $($F.VpW)x$($F.VpH) vs panel $($F.ScreenW)x$($F.ScreenH)   clip $clip"
        if ($script:VpImeClipped) {
            Write-Host "             bottom cut at the keyboard: positions are against what you can see" -ForegroundColor DarkCyan
        }
        Write-Host "             counted $($F.Visible) of $($F.Nodes) nodes  (scrolled out $($F.OffScreen), hidden $($F.Hidden))"
        if (-not $F.VpTrusted) {
            Write-Host "             geometry is advisory only; counts are over the whole tree" -ForegroundColor DarkYellow
        }
        if ($F.Nodes -lt 10) {
            Write-Host "  WARNING    only $($F.Nodes) nodes parsed - check -DumpOpt, then -Explain" -ForegroundColor Red
        }
        Write-Host "  RENDER     overdraw $($F.Overdraw)x  boxes $($F.Boxes)  largest leaf $($F.LargestFrac)/1000 aspect $($F.LargestAspect)"
        Write-Host "             fx $($F.FxScore) (blur $($F.FxBlur), shadow $($F.FxShadow), gradient $($F.FxGradient), clip $($F.FxClip))"
        Write-Host "  DEMAND     declared rate $($F.DeclRate)  lazy $($F.Lazy)  reusable $($F.Reusable)  cached $($F.Cached)"
        Write-Host "  CHURN      $($churn.Shape)  +$($churn.Added) -$($churn.Removed) net $($churn.Net)  over $($churn.Dt)s = $($churn.Rate)/s"
        Write-Host "  STABLE     $($script:StableTicks) ticks in this class"
    }

    $truth = $Label
    $truthSrc = if ($Label) { "manual" } else { "" }
    # -Label AUTO is gone with the verdict it depended on. A machine-written
    # label that is wrong poisons the fit silently and is never noticed; a
    # missing label costs one screen. Labels are yours now, all of them.
    if ($Label -eq "AUTO") {
        Write-Host "  -Label AUTO was removed: process evidence cannot label a screen." -ForegroundColor Yellow
        Write-Host "  Use -Surfaces to see what is on it, then label it yourself." -ForegroundColor DarkGray
        $truth = ""; $truthSrc = ""
    }
    if ($Deep) {
        Invoke-DeepProbe -Id $Id -ProcId ([string]$win.Pid) -WinName $name
    }

    if ($Explain) {
        Write-Host ""
        Write-Host "  PARSED NODES (first 25) - tag, indent, rect, state:"
        $k = 0
        foreach ($n in $F.NodeList) {
            if ($k -ge 25) { break }
            $rect = if ($n.Rect) { "[{0:N0},{1:N0}]-[{2:N0},{3:N0}]" -f $n.Rect.L,$n.Rect.T,$n.Rect.R,$n.Rect.B } else { "no rect" }
            $state = if ($n.VisW -gt 0) { "on" } elseif ($n.Rect) { "OFF" } else { "-" }
            Write-Host ("    {0,-3} {1,-20} ind {2,-3} {3,-30} {4}" -f $k, $n.Tag, $n.Indent, $rect, $state)
            $k++
        }
    }

    if ($Raw) {
        $f = Join-Path (Get-Location) ("tree_w{0}.txt" -f $Id)
        $lines | Set-Content -Path $f -Encoding UTF8
        Write-Host "  raw -> $f"
        $allTotal = 0; foreach ($v in $F.TallyAll.Values) { $allTotal += $v }
        Write-Host "  DOCUMENT   $($F.Total) tags on screen of $allTotal in the whole tree"
        Write-Host "  TOP TAGS (on-screen only; should be component names):"
        $F.Tally.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 12 |
            ForEach-Object { Write-Host ("    {0,6}  {1}" -f $_.Value, $_.Key) }
    }

    return [PSCustomObject]@{
        host_ts = (Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff")
        dev_mono = [math]::Round($mono, 3)
        window = $Id; name = $name
        scene_scope = ($script:SceneContext -join "|")
        win_share = if ($win -and $win.Share -ge 0) { [int]($win.Share * 100) } else { -1 }
        pid = if ($win) { $win.Pid } else { "" }
        scene = $R.Class; candidates = ($R.Cand -join "|"); confidence = $R.Conf
        score = $R.Score; margin = $R.Margin; ranked = $R.Ranked
        truth = $truth; truth_src = $truthSrc
        modifiers = ($mods -join "|"); stable_ticks = $script:StableTicks
        total = $F.Total; text = $F.Text; image = $F.Image; icon = $F.Icon
        button = $F.Button
        slider = $F.Slider; progress = $F.Progress; toggle = $F.Toggle
        checkable = $F.Checkable
        icon_left = $F.IconLeft; wide_img = $F.WideImg; img_geo = $F.ImgGeo
        cover_opaque = $F.CoverOpaque; cover_web = $F.CoverWeb; cover_xc = $F.CoverXc
        cover_emb = $F.CoverEmb; described = $F.Described
        surf_frac = $F.SurfFrac; surf_covered = $F.SurfCovered
        editable = $F.Editable; listlike = $F.ListLike; gridlike = $F.GridLike
        swiper = $F.Swiper; scroll = $F.Scroll; scrollers = $scrollers
        web = $F.Web; xcomponent = $F.XComponent; video = $F.Video; canvas = $F.Canvas
        opaque = ($F.Web + $F.XComponent + $F.Embedded)
        embedded = $F.Embedded
        textlen = $F.TextLen; textmax = $F.TextMax; avgtext = $F.AvgText
        edit_pos = $F.EditPos; slider_pos = $F.SliderPos; pos_source = $F.PosSource
        nodes = $F.Nodes; offscreen = $F.OffScreen; hidden = $F.Hidden
        geo = [int]$F.GeoOK; vp_w = $F.VpW; vp_h = $F.VpH
        boxes = $F.Boxes; visible_boxes = $F.Visible; overdraw = $F.Overdraw
        largest_frac = $F.LargestFrac; largest_aspect = $F.LargestAspect
        xc_name = $F.XcName; xc_lib = $F.XcLib; xc_type = $F.XcType
        xc_a11y = $F.XcA11y; xc_secure = $F.XcSecure; xc_hdr = $F.XcHdr
        xc_aspect = $F.XcAspect; xc_hint = $F.XcHint; xc_conf = $F.XcConf
        xc_flags = $F.XcFlags
        fx_score = $F.FxScore; fx_blur = $F.FxBlur; fx_shadow = $F.FxShadow
        fx_opacity = $F.FxOpacity; fx_clip = $F.FxClip; fx_gradient = $F.FxGradient
        decl_rate = $F.DeclRate; lazy = $F.Lazy; reusable = $F.Reusable; cached = $F.Cached
        churn_delta = $churn.Delta; churn_added = $churn.Added
        churn_removed = $churn.Removed; churn_net = $churn.Net
        churn_shape = $churn.Shape; churn_rate = $churn.Rate; dt = $churn.Dt
        evidence = ($R.Ev -join "; ")
    }
}

# ---------------------------------------------------------------- separation
#
# The classifier is an additive scorer and the answer is an argmax, so a class
# is only ever as good as its DISTANCE from the next one. Two failures look
# identical on the console and have nothing in common underneath:
#
#   shared drivers   two classes are scored by the same gates, so no screen
#                    can ever separate them. Measured by the cosine between
#                    their weight vectors, and by exclusive mass - the score a
#                    class can earn from gates that score nothing else. A class
#                    with no exclusive mass cannot be chosen on its own
#                    evidence, only on somebody else's absence.
#
#   co-firing gates  two classes share no weight at all, cosine 0, and still
#                    collide because their gates both open on the same screen.
#                    Static analysis cannot see this; the labelled corpus can,
#                    and that is what the -Calib half of this mode is for.
#
# Both are reported here as numbers rather than impressions, because "the
# classes feel noisy" is not actionable and "ICON_GRID has zero exclusive mass
# and a cosine of 0.75 with ICON_PAGER" is.

function Get-RuleMatrix {
    # Parse THIS file's Get-SceneClass for its Score calls and group them by
    # the gate that encloses them. The matrix is read off the source rather
    # than maintained by hand, so it cannot drift from the rules it describes.
    $lines = @(Get-Content -LiteralPath $PSCommandPath)

    $strip = {
        param([string] $l)
        $sb = New-Object System.Text.StringBuilder
        $inq = $false; $q = [char]0
        for ($j = 0; $j -lt $l.Length; $j++) {
            $c = $l[$j]
            if ($inq) {
                if ($c -eq '`') { $j++ } elseif ($c -eq $q) { $inq = $false }
                continue
            }
            if ($c -eq '"' -or $c -eq "'") { $inq = $true; $q = $c; continue }
            if ($c -eq '#') { break }
            [void]$sb.Append($c)
        }
        return $sb.ToString()
    }

    $start = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -like 'function Get-SceneClass*') { $start = $i; break }
    }
    if ($start -lt 0) { return $null }
    $d = 0; $end = $lines.Count - 1
    for ($i = $start; $i -lt $lines.Count; $i++) {
        $code = & $strip $lines[$i]
        $d += ([regex]::Matches($code, '\{')).Count - ([regex]::Matches($code, '\}')).Count
        if ($i -gt $start -and $d -eq 0) { $end = $i; break }
    }

    $pat   = [regex] 'Score\s+"([A-Z_]+)"\s+(\(?-?\d+(?:\.\d+)?\)?|\$\w+|\(\$[^)]*\))'
    $W     = @{}      # gate id -> class -> weight
    $Gate  = @{}      # gate id -> the condition that opens it
    $fuzzy = @()      # Score calls whose weight is not a literal
    $stack = New-Object System.Collections.ArrayList
    $d = 0
    for ($i = $start + 1; $i -le $end; $i++) {
        $raw = $lines[$i]; $code = & $strip $raw
        $o = ([regex]::Matches($code, '\{')).Count
        $c = ([regex]::Matches($code, '\}')).Count
        # Two shapes open and close on the same line and so never reach the
        # stack: a one-line `if (...) { Score ... }` and a one-line switch arm
        # `"CAMERA" { Score ... }`. Attributed to whatever block contained
        # them they invent shared gates that do not exist, which is the exact
        # opposite of the error this mode exists to find - the switch arms are
        # mutually exclusive and were being reported as one gate scoring five
        # classes together. Any balanced braced line carrying a Score is its
        # own gate.
        $hasScore = $pat.IsMatch($raw)
        # `} else {` is balanced too, but it CLOSES one branch and OPENS
        # another at the same depth, so the stack neither pops nor pushes and
        # the else branch lands in the if branch's gate. That merge cancelled
        # the pager discriminator against itself: +2.5 to one class and -2.0
        # to the other, summed inside one gate, came out as +0.5/+0.5 and the
        # pair still read as having no exclusive evidence.
        $isElse = ($code -match '^\s*\}\s*(else|elseif)\b')
        if ($isElse -and $o -eq $c -and $o -gt 0) {
            $dep = if ($stack.Count -gt 0) { $stack[$stack.Count-1].Depth } else { $d }
            if ($stack.Count -gt 0) { $stack.RemoveAt($stack.Count-1) }
            [void]$stack.Add([PSCustomObject]@{ Depth = $dep; Id = $i + 1 })
            $Gate[$i+1] = $raw.Trim()
        }
        $inline = ($o -eq $c -and $o -gt 0 -and $hasScore -and -not $isElse)
        foreach ($m in $pat.Matches($raw)) {
            $cls = $m.Groups[1].Value
            $wt  = $m.Groups[2].Value.Trim('(', ')')
            # -as, not TryParse: TryParse reads the current culture and
            # would miss "4.5" on a comma-decimal machine, silently turning
            # every literal weight into a nominal one.
            # $wv, not $w: PowerShell variable names are case-INSENSITIVE,
            # so $w and the $W matrix above are one variable, and the first
            # weight parsed overwrote the whole matrix with a double.
            $wv  = $wt -as [double]
            if ($null -eq $wv) {
                # A computed weight (the surface-hint switch scales by stated
                # confidence). Counted at a nominal 2.0 and reported, so the
                # ceiling is honest about being an estimate for that class.
                $wv = 2.0; $fuzzy += "line $($i+1): $cls $wt"
            }
            $gid = if ($inline) { $i + 1 } elseif ($stack.Count -gt 0) { $stack[$stack.Count-1].Id } else { 0 }
            if (-not $W.ContainsKey($gid)) { $W[$gid] = @{}; $Gate[$gid] = $raw.Trim() }
            if (-not $W[$gid].ContainsKey($cls)) { $W[$gid][$cls] = 0.0 }
            $W[$gid][$cls] += $wv
        }
        if ($o -gt $c -and -not $isElse) { [void]$stack.Add([PSCustomObject]@{ Depth = $d; Id = $i + 1 }); $Gate[$i+1] = $raw.Trim() }
        $d += $o - $c
        while ($stack.Count -gt 0 -and $d -le $stack[$stack.Count-1].Depth) { $stack.RemoveAt($stack.Count-1) }
    }

    $classes = @($W.Values | ForEach-Object { $_.Keys } | Select-Object -Unique | Sort-Object)
    return [PSCustomObject]@{ W = $W; Gate = $Gate; Classes = $classes; Gates = @($W.Keys); Fuzzy = $fuzzy }
}

function Show-Separation {
    param([string] $Calib)

    $M = Get-RuleMatrix
    if (-not $M) { Write-Host "could not parse the scorer" -ForegroundColor Red; return }
    $W = $M.W; $classes = $M.Classes; $gates = $M.Gates

    $ceil = @{}; $excl = @{}; $ngate = @{}
    foreach ($c in $classes) {
        $t = 0.0; $e = 0.0; $n = 0
        foreach ($g in $gates) {
            $v = 0.0
            if ($W[$g].ContainsKey($c)) { $v = $W[$g][$c] }
            if ($v -le 0) { continue }
            $t += $v; $n++
            $sharers = 0
            foreach ($k in $W[$g].Keys) { if ($W[$g][$k] -gt 0) { $sharers++ } }
            if ($sharers -eq 1) { $e += $v }
        }
        $ceil[$c] = $t; $excl[$c] = $e; $ngate[$c] = $n
    }

    Write-Host ""
    Write-Host "CLASS SEPARATION" -ForegroundColor Cyan
    Write-Host ("  {0} classes, {1} gates, {2} Score calls" -f $classes.Count, $gates.Count,
                (($gates | ForEach-Object { $W[$_].Count }) | Measure-Object -Sum).Sum) -ForegroundColor DarkGray
    if (@($M.Fuzzy).Count -gt 0) {
        Write-Host ("  {0} weights are computed, not literal - counted at a nominal 2.0:" -f @($M.Fuzzy).Count) -ForegroundColor DarkGray
        foreach ($f in @($M.Fuzzy)) { Write-Host "      $f" -ForegroundColor DarkGray }
    }

    # ---- ceilings ---------------------------------------------------------
    # The decision is an argmax over these totals. If the ceilings differ, the
    # argmax is comparing classes that cannot reach the same number, and the
    # one with more rules written for it wins by bookkeeping.
    Write-Host ""
    Write-Host "  ceiling - the most each class can ever score" -ForegroundColor White
    Write-Host ("  {0,-20} {1,5} {2,7} {3,10} {4,8}   {5}" -f "class", "gates", "ceiling", "exclusive", "shared", "")
    $cap = $script:P.ScoreCap
    foreach ($c in ($classes | Sort-Object { -$ceil[$_] })) {
        $note = ""
        if ($excl[$c] -le 0.001) { $note = "cannot be chosen on its own evidence" }
        elseif ($ceil[$c] -lt ($cap * 0.6)) { $note = "under-specified - cannot reach the cap" }
        elseif ($ceil[$c] -gt $cap) { $note = "capped at $cap" }
        $col = if ($excl[$c] -le 0.001) { "Red" } elseif ($ceil[$c] -lt ($cap * 0.6)) { "Yellow" } else { "Gray" }
        Write-Host ("  {0,-20} {1,5} {2,7:n1} {3,10:n1} {4,8:n1}   {5}" -f `
                    $c, $ngate[$c], $ceil[$c], $excl[$c], ($ceil[$c] - $excl[$c]), $note) -ForegroundColor $col
    }

    # ---- shared drivers ---------------------------------------------------
    # Cosine between the two classes' weight vectors over the gates. 1.0 means
    # every gate that scores one scores the other in the same proportion: no
    # screen can separate them, ever, and no threshold sweep will help.
    Write-Host ""
    Write-Host "  shared drivers - cosine between class weight vectors" -ForegroundColor White
    $pairs = @()
    for ($i = 0; $i -lt $classes.Count; $i++) {
        for ($j = $i + 1; $j -lt $classes.Count; $j++) {
            $a = $classes[$i]; $b = $classes[$j]
            $dot = 0.0; $na = 0.0; $nb = 0.0
            $shared = @()
            foreach ($g in $gates) {
                $x = 0.0; $y = 0.0
                if ($W[$g].ContainsKey($a)) { $x = $W[$g][$a] }
                if ($W[$g].ContainsKey($b)) { $y = $W[$g][$b] }
                $dot += $x * $y; $na += $x * $x; $nb += $y * $y
                if ($x -ne 0 -and $y -ne 0) { $shared += ("{0}({1:n1}/{2:n1})" -f $g, $x, $y) }
            }
            if ($na -le 0 -or $nb -le 0) { continue }
            $cos = $dot / ([math]::Sqrt($na) * [math]::Sqrt($nb))
            # The largest lead a can ever build over b: an upper bound, since
            # it assumes every favourable gate can open at once. A SMALL value
            # is the sound claim - it proves the pair is fragile. A large one
            # proves nothing, because the gates may be mutually exclusive.
            $lab = 0.0; $lba = 0.0
            foreach ($g in $gates) {
                $x = 0.0; $y = 0.0
                if ($W[$g].ContainsKey($a)) { $x = $W[$g][$a] }
                if ($W[$g].ContainsKey($b)) { $y = $W[$g][$b] }
                $lab += [math]::Max(0.0, $x - $y); $lba += [math]::Max(0.0, $y - $x)
            }
            $pairs += [PSCustomObject]@{ A=$a; B=$b; Cos=$cos; Lead=[math]::Min($lab,$lba); Shared=($shared -join " ") }
        }
    }
    foreach ($p in ($pairs | Where-Object { $_.Cos -gt 0.10 } | Sort-Object Cos -Descending | Select-Object -First 10)) {
        $col = if ($p.Cos -ge 0.5) { "Red" } elseif ($p.Cos -ge 0.25) { "Yellow" } else { "Gray" }
        Write-Host ("  cos {0,5:n2}  {1,-18}{2,-18} gates {3}" -f $p.Cos, $p.A, $p.B, $p.Shared) -ForegroundColor $col
    }
    $frag = @($pairs | Where-Object { $_.Lead -lt 2.0 -and $_.Cos -gt 0.10 })
    if ($frag.Count -gt 0) {
        Write-Host ""
        Write-Host "  neither side can build a lead of 2.0 over the other:" -ForegroundColor Yellow
        foreach ($p in ($frag | Sort-Object Lead | Select-Object -First 8)) {
            Write-Host ("    {0,-18}{1,-18} best lead {2:n1}" -f $p.A, $p.B, $p.Lead) -ForegroundColor Yellow
        }
    }

    if (-not $Calib -or -not (Test-Path $Calib)) {
        Write-Host ""
        Write-Host "  the rest needs labelled screens:  scene_class.cmd -Separation -Calib uitest.csv" -ForegroundColor DarkGray
        Write-Host ""
        return
    }

    # ---- co-firing, measured ----------------------------------------------
    # Two classes with cosine 0 still collide when their gates open on the
    # same screen. That is a property of the screens, not of the weights, so
    # it can only be measured - this is the half of the question the static
    # matrix above cannot answer.
    $rows = @(Import-Csv $Calib | Where-Object { $_.truth -and $_.truth -ne "" })
    if ($rows.Count -lt 4) { Write-Host "  too few labelled rows for the empirical half" -ForegroundColor Yellow; return }

    # Rows are not interchangeable, and pooling them hides it. A row captured
    # before a feature existed carries no COLUMN for it, and Import-Csv hands
    # back $null rather than a zero - so every rule that needed it stood down
    # when that row was replayed. Pooled with fresh rows it does not read as a
    # missing measurement, it reads as a measurement of zero, and the pair it
    # was supposed to separate comes out at 0.0 sigma. That is a fact about
    # the corpus, not about the classifier, and the two must not be reported
    # as one number.
    $fresh = @($rows | Where-Object { $null -ne $_.icon_left -and $null -ne $_.wide_img })
    $cover = @($rows | Where-Object { $null -ne $_.cover_opaque })
    $geoN  = @($rows | Where-Object { $_.pos_source -and $_.pos_source -ne "none" })
    Write-Host ""
    Write-Host ("  corpus   {0} rows   {1} with rects   {2} with row geometry   {3} with coverage" -f `
                $rows.Count, $geoN.Count, $fresh.Count, $cover.Count) -ForegroundColor White
    if ($fresh.Count -lt $rows.Count) {
        Write-Host ("  {0} rows predate icon_left/wide_img: on those rows ICON_LIST and FEED" -f ($rows.Count - $fresh.Count)) -ForegroundColor Yellow
        Write-Host  "  cannot be scored from row geometry at all, and the row that could have" -ForegroundColor Yellow
        Write-Host  "  separated them reads as a row that says they are the same." -ForegroundColor Yellow
    }

    Show-PairSeparation -Rows $rows -Title ("all {0} rows" -f $rows.Count)
    if ($fresh.Count -ge 10 -and $fresh.Count -lt $rows.Count) {
        Show-PairSeparation -Rows $fresh -Title ("the {0} rows that carry row geometry" -f $fresh.Count)
    }

    Write-Host ""
    Write-Host "  below 1.0 sigma the boundary is noise: the fix is a new feature," -ForegroundColor DarkGray
    Write-Host "  not a new weight. Above 2.0 the features already separate them and" -ForegroundColor DarkGray
    Write-Host "  a wrong answer there is a rule bug you can find by hand." -ForegroundColor DarkGray
    Write-Host ""
}

function Show-PairSeparation {
    param($Rows, [string] $Title)

    $scores = @{}   # truth -> list of score hashtables
    $conf   = @{}   # "truth->got" -> count
    $ok     = 0
    foreach ($r in $Rows) {
        $f = Convert-RowToFeatures $r
        $res = Get-SceneClass -F $f -WinName ([string]$r.name)
        $t = [string]$r.truth
        if (-not $scores.ContainsKey($t)) { $scores[$t] = @() }
        # A row that matched nothing returns early without an All table. It is
        # still a row, and dropping it would quietly flatter the separation.
        $all = if ($res.All) { $res.All } else { @{} }
        $scores[$t] += ,$all
        if ($res.Class -eq $t) { $ok++ }
        else {
            $k = "$t->$($res.Class)"
            if (-not $conf.ContainsKey($k)) { $conf[$k] = 0 }
            $conf[$k]++
        }
    }

    Write-Host ""
    Write-Host ("  === {0}: {1}/{2} correct ({3}%)" -f $Title, $ok, @($Rows).Count,
                [int](100.0 * $ok / [math]::Max(1, @($Rows).Count))) -ForegroundColor Cyan
    if ($conf.Count -eq 0) { Write-Host "  no confusions" -ForegroundColor Green }

    # Fisher separation on the one quantity the argmax actually uses: the
    # score DIFFERENCE between the two classes. How many standard deviations
    # apart are the two populations on that difference? Below 1 the pair is
    # noise whatever the console says; above 2 it is a real boundary.
    $sepOf = {
        param($a, $b)
        if (-not $scores.ContainsKey($a) -or -not $scores.ContainsKey($b)) { return $null }
        $da = @(); $db = @()
        foreach ($s in $scores[$a]) { $x=0.0; $y=0.0; if($s.ContainsKey($a)){$x=$s[$a]}; if($s.ContainsKey($b)){$y=$s[$b]}; $da += ($x-$y) }
        foreach ($s in $scores[$b]) { $x=0.0; $y=0.0; if($s.ContainsKey($a)){$x=$s[$a]}; if($s.ContainsKey($b)){$y=$s[$b]}; $db += ($x-$y) }
        if ($da.Count -lt 2 -or $db.Count -lt 2) { return $null }
        $ma = ($da | Measure-Object -Average).Average
        $mb = ($db | Measure-Object -Average).Average
        $va = (($da | ForEach-Object { ($_ - $ma) * ($_ - $ma) }) | Measure-Object -Sum).Sum / ($da.Count - 1)
        $vb = (($db | ForEach-Object { ($_ - $mb) * ($_ - $mb) }) | Measure-Object -Sum).Sum / ($db.Count - 1)
        $den = [math]::Sqrt($va + $vb)
        if ($den -lt 0.001) { if ([math]::Abs($ma - $mb) -lt 0.001) { return 0.0 } else { return 99.0 } }
        return ([math]::Abs($ma - $mb) / $den)
    }

    foreach ($k in ($conf.Keys | Sort-Object { -$conf[$_] })) {
        $a, $b = $k -split '->'
        $sv = & $sepOf $a $b
        $txt = if ($null -eq $sv) { "no counter-examples labelled - cannot measure" }
               elseif ($sv -lt 1.0) { "{0:n1} sigma - NOISE, the two populations overlap" -f $sv }
               elseif ($sv -lt 2.0) { "{0:n1} sigma - weak" -f $sv }
               else { "{0:n1} sigma - separable, the rule is just wrong" -f $sv }
        $col = if ($null -eq $sv) { "DarkGray" } elseif ($sv -lt 1.0) { "Red" } elseif ($sv -lt 2.0) { "Yellow" } else { "Gray" }
        Write-Host ("  {0,3}x  {1,-18} read as {2,-18} {3}" -f $conf[$k], $a, $b, $txt) -ForegroundColor $col
    }

    # Every pair of labelled classes, not only the ones that went wrong: a
    # pair that happens to be right today on 1.3 sigma will go wrong tomorrow.
    Write-Host ""
    Write-Host "  every labelled pair, by separation on the deciding difference" -ForegroundColor White
    $labs = @($scores.Keys | Sort-Object)
    $tbl = @()
    for ($i = 0; $i -lt $labs.Count; $i++) {
        for ($j = $i + 1; $j -lt $labs.Count; $j++) {
            $sv = & $sepOf $labs[$i] $labs[$j]
            if ($null -ne $sv) {
                $tbl += [PSCustomObject]@{ A=$labs[$i]; B=$labs[$j]; S=$sv
                                           N=("{0}/{1}" -f @($scores[$labs[$i]]).Count, @($scores[$labs[$j]]).Count) }
            }
        }
    }
    foreach ($t in ($tbl | Sort-Object S | Select-Object -First 12)) {
        $col = if ($t.S -lt 1.0) { "Red" } elseif ($t.S -lt 2.0) { "Yellow" } else { "Gray" }
        Write-Host ("  {0,5:n1} sigma  {1,-18}{2,-18} n {3}" -f $t.S, $t.A, $t.B, $t.N) -ForegroundColor $col
    }

}


# ---------------------------------------------------------------- attributes
#
# What the dump actually spells. Two fx fixes in a row missed because the
# keyword was guessed rather than read: clip was "clip", blur was
# backgroundBlurStyle, and a scan written against a guess matches a key name
# instead of a value and reports an effect on every node on screen. The dump
# is too large to send off an airgapped box, so the script has to summarise
# it: every distinct attribute key, how many nodes carry it, and the distinct
# values it takes. Small enough to read, specific enough to write a rule on.
function Show-Attrs {
    param([int] $Id)

    $lines = Get-Tree -Id $Id
    if (-not $lines -or $lines.Count -lt 2) { Write-Host "empty tree for window $Id" -ForegroundColor Yellow; return }

    $keys = @{}
    $pat  = [regex] '"([A-Za-z0-9_]+)"\s*:\s*("(?:[^"\\]|\\.)*"|[^,}\]]+)'
    foreach ($l in $lines) {
        $seen = @{}
        foreach ($m in $pat.Matches($l)) {
            $k = $m.Groups[1].Value
            $v = $m.Groups[2].Value.Trim().Trim('"')
            if ($v.Length -gt 24) { $v = $v.Substring(0, 24) + "..." }
            if (-not $keys.ContainsKey($k)) { $keys[$k] = @{ N = 0; V = @{} } }
            if (-not $seen.ContainsKey($k)) { $keys[$k].N++; $seen[$k] = $true }
            if ($keys[$k].V.Count -lt 6) { $keys[$k].V[$v] = $true }
        }
    }

    Write-Host ""
    if ($keys.Count -eq 0) {
        Write-Host "no `"key`": value pairs in this dump - it is not the JSON shape." -ForegroundColor Yellow
        Write-Host "the first lines, verbatim:" -ForegroundColor DarkGray
        foreach ($l in ($lines | Select-Object -First 6)) { Write-Host ("  " + $l.Trim()) -ForegroundColor DarkGray }
        return
    }

    Write-Host ("ATTRIBUTES  window {0}   {1} lines   {2} distinct keys" -f $Id, $lines.Count, $keys.Count) -ForegroundColor Cyan
    $fxPat = '(?i)blur|shadow|opacity|clip|mask|gradient|radius|effect|bright|saturat'
    foreach ($group in @(
        @{ T = "effects - what the render-cost scan reads"; P = $fxPat; Col = "White" },
        @{ T = "everything else";                          P = $null;  Col = "DarkGray" })) {
        $names = @($keys.Keys | Where-Object {
            if ($group.P) { $_ -match $group.P } else { $_ -notmatch $fxPat }
        } | Sort-Object)
        if ($names.Count -eq 0) { continue }
        Write-Host ""
        Write-Host ("  {0}" -f $group.T) -ForegroundColor $group.Col
        foreach ($k in $names) {
            $vals = (@($keys[$k].V.Keys | Sort-Object) -join " | ")
            if ($vals.Length -gt 90) { $vals = $vals.Substring(0, 90) + " ..." }
            Write-Host ("    {0,-28} {1,5} nodes   {2}" -f $k, $keys[$k].N, $vals)
        }
    }
    Write-Host ""
}


# ================================================================== main

Import-Thresholds

# Which copy of this file is this? The script moves between machines by hand,
# so "did I copy the new one" is a real question with no good answer unless the
# file can state its own identity. The hash is of the file itself, so it cannot
# drift from a version constant somebody forgot to bump.
if ($Version) {
    $self = $PSCommandPath
    $h = (Get-FileHash -Path $self -Algorithm SHA256).Hash.Substring(0, 12).ToLower()
    $n = (Get-Content -Path $self).Count
    Write-Host ""
    Write-Host ("scene_class.ps1   {0} lines   sha256 {1}" -f $n, $h) -ForegroundColor Cyan
    Write-Host ("  {0}" -f $self) -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  scopes       -Window  -Screen  -Classify  -ListWindows"
    Write-Host "  classify     -Watch  -Out  -Label  -Fit  -Apply  -Calib"
    Write-Host "  validate     -Separation"
    Write-Host "  discovery    -FindRects  -RsProbe  -RsFps  -Deep  -Services"
    Write-Host "  diagnostics  -DumpOpt  -Explain  -Raw  -ShowCmd  -WindowId  -Attrs"
    Write-Host ""
    Write-Host "  repo  https://github.com/reiniertl/arkui" -ForegroundColor DarkGray
    exit 0
}

# -Fit runs entirely offline against the labelled rows. No phone, no hdc.
if ($Fit) { Invoke-Fit -Path $Calib -Apply:$Apply; exit 0 }

# -Separation reads the scorer itself. The static half needs nothing at all;
# the empirical half needs the corpus. Neither needs the phone.
if ($Separation) { Show-Separation -Calib $Calib; exit 0 }

if (-not (Get-Command hdc -ErrorAction SilentlyContinue)) {
    Write-Host "error: hdc not on PATH" -ForegroundColor Red; exit 1
}

$table = Get-WindowTable
Repair-WindowRects -Table $table
if ($table.Count -eq 0) {
    Write-Host "error: could not read the window table; pass -WindowId" -ForegroundColor Red; exit 1
}

if ($Attrs) {
    $id = $WindowId
    if ($id -le 0) { $fgw = Resolve-Scene -Table $table -Quiet; if ($fgw) { $id = $fgw.WinId } }
    Set-WinRect -Row ($table | Where-Object { $_.WinId -eq $id } | Select-Object -First 1)
    Show-Attrs -Id $id; exit 0
}
if ($Services) { Show-Services; exit 0 }
if ($RsProbe) { Show-RsProbe; exit 0 }
if ($RsFps)   { Show-RsFps -Layer $RsFps; exit 0 }

# The display: every window, every process, in one call. Nothing inside ArkUI
# can produce this view - a container sees its own window and no other - so
# this is the script's one unique signal, and the aggregator's job later.
if ($ListWindows) {
    $host_ = Resolve-Scene -Table $table -Quiet
    $panel = if ($script:ScreenW -gt 0) { "{0}x{1}" -f $script:ScreenW, $script:ScreenH } else { "unknown" }
    Write-Host ""
    Write-Host ("DISPLAY   panel {0}   {1} windows" -f $panel, $table.Count) -ForegroundColor Cyan
    Write-Host ""
    Write-Host ("  {0,-5} {1,-28} {2,-6} {3,-20} {4,-7} {5,-7} {6,-8} {7}" -f `
                "win", "name", "z", "rect", "share", "unocc", "role", "pid")
    foreach ($w in ($table | Sort-Object ZOrd -Descending)) {
        $rect  = if ($w.W -gt 0) { "{0},{1} {2}x{3}" -f $w.X, $w.Y, $w.W, $w.H } else { "-" }
        $share = if ($null -ne $w.Share -and $w.Share -ge 0) { "{0}%" -f [int]($w.Share * 100) } else { "?" }
        # share is how big it is; unocc is how much of the panel it actually
        # reaches. A window at 100% share and 0% unocc is completely hidden,
        # and the two columns differing is the whole point of printing both.
        $un    = if ($null -ne $w.Unocc -and $w.Unocc -ge 0) { "{0}%" -f [int]($w.Unocc * 100 + 0.5) } else { "?" }
        $role  = if ($host_ -and $w.WinId -eq $host_.WinId) { "HOST" }
                 elseif ($null -eq $w.Share) { "hidden" }
                 elseif ($w.IsOverlay) { "overlay" } else { "content" }
        $col   = if ($role -eq "HOST") { "Green" } elseif ($role -eq "content") { "Gray" } else { "DarkGray" }
        Write-Host ("  {0,-5} {1,-28} {2,-6} {3,-20} {4,-7} {5,-7} {6,-8} {7}" -f `
                    $w.WinId, $w.Name, $w.ZOrd, $rect, $share, $un, $role, $w.Pid) -ForegroundColor $col
    }
    Write-Host ""
    # The composition, as one line. A single opaque window covering the panel
    # can go to a hardware overlay plane and skip GPU composition entirely;
    # the moment a sheet and an IME land on top of it, that path is gone and
    # every frame is blended. That is a power difference no tree can show.
    $reach = @($table | Where-Object { $null -ne $_.Unocc -and $_.Unocc -ge 0.01 })
    $topw  = $reach | Sort-Object Unocc -Descending | Select-Object -First 1
    Write-Host ("  composition  {0} windows reach the panel" -f $reach.Count) -ForegroundColor Cyan
    if ($topw) {
        Write-Host ("               largest is {0} at {1}% - {2}" -f $topw.Name, [int]($topw.Unocc * 100 + 0.5),
                    $(if ($topw.Unocc -ge 0.97) { "one window owns the display" }
                      else { "no window owns the display, so every frame is composited" })) -ForegroundColor DarkGray
    }
    if ($script:SceneContext -contains "OVERLAPPING_FULLSCREEN") {
        Write-Host "               rects overlap well past the panel: some of these are transparent" -ForegroundColor Yellow
        Write-Host "               and the table does not say so, so unocc is an upper bound for the" -ForegroundColor Yellow
        Write-Host "               top window and a lower bound for the ones under it." -ForegroundColor Yellow
    }
    if ($script:SceneContext -and $script:SceneContext.Count -gt 0) {
        Write-Host ("  context  {0}" -f ($script:SceneContext -join ", ")) -ForegroundColor DarkCyan
    }
    Write-Host ""
    Write-Host ("  then:  scene_class.cmd -Window -Screen -WindowId <win>") -ForegroundColor DarkGray
    exit 0
}

$FollowForeground = ($WindowId -le 0)
if ($FollowForeground) {
    $fg = Resolve-Scene -Table $table
    if (-not $fg) { Write-Host "error: no window found; pass -WindowId" -ForegroundColor Red; exit 1 }
    $WindowId = $fg.WinId
}

# The two scope views. Both may be asked for at once, which prints the window
# and the screen back to back - the comparison is the whole point.
if ($FindRects) { Show-FindRects -Id $WindowId; exit 0 }

if ($Window -or $Screen) {
    $w = @($table | Where-Object { $_.WinId -eq $WindowId })[0]
    if (-not $w) { Write-Host "error: window $WindowId not in the table" -ForegroundColor Red; exit 1 }
    Set-WinRect -Row $w
    $lines = Get-Tree -Id $WindowId
    $Fs = Get-Features -Lines $lines              # clipped, when the viewport is trusted
    # Only worth a second pass when the clip actually did something. With no
    # rects the two scopes are the same tree and the same answer.
    $Fw = if ($Fs.VpTrusted) { Get-Features -Lines $lines -NoClip } else { $Fs }

    if ($Window) {
        Show-Scope -F $Fs -Id $WindowId -WinName $w.Name -ProcId ([string]$w.Pid) -Mode "window"
        if ($Classify) { Show-ScopeClass -F $Fw -Mode "window" -Win $w }
    }
    if ($Screen) {
        Show-Scope -F $Fs -Id $WindowId -WinName $w.Name -ProcId ([string]$w.Pid) -Mode "screen"
        if ($Classify) { Show-ScopeClass -F $Fs -Mode "screen" -Win $w }
    }
    if ($Classify -and $Window -and $Screen -and -not $Fs.VpTrusted) {
        Write-Host ""
        Write-Host "  both classes come from the same counts: with no rects there is only" -ForegroundColor DarkYellow
        Write-Host "  one tree to classify. They will agree until geometry is available." -ForegroundColor DarkYellow
    }
    exit 0
}

function Save-Row { param($r)
    if (-not $r) { return }
    # -Label always lands in the calibration file, whether or not -Out is set.
    $targets = @()
    if ($Out)   { $targets += $Out }
    if ($Label -and $r.truth) { $targets += $Calib }
    foreach ($t in ($targets | Select-Object -Unique)) {
        if (Test-Path $t) { $r | Export-Csv -Path $t -NoTypeInformation -Append }
        else              { $r | Export-Csv -Path $t -NoTypeInformation }
    }
}

if ($Watch -gt 0) {
    Write-Host "watching every ${Watch}s - Ctrl+C to stop"
    Write-Host ("{0,11}  {1,-18} {2,-40} {3,-6} {4}" -f "dev_mono","class","modifiers","churn","window")
    while ($true) {
        if ($FollowForeground) {
            $table = Get-WindowTable
            $fg = Resolve-Scene -Table $table -Quiet
            if ($fg -and $fg.WinId -ne $WindowId) {
                # Require two consecutive ticks before re-scoping. A scene that
                # flickers between two windows produces a flickering class, and
                # a flickering class makes the governor oscillate - which is
                # the exact failure this project exists to avoid.
                if ($script:PendingWin -eq $fg.WinId) {
                    $WindowId = $fg.WinId
                    $script:PrevTally = $null     # scene changed: churn restarts
                    $script:PendingWin = 0
                } else {
                    $script:PendingWin = $fg.WinId
                }
            } else { $script:PendingWin = 0 }
        }
        Save-Row (Invoke-Classify -Id $WindowId -Table $table -Terse)
        Start-Sleep -Seconds $Watch
    }
}

$r = Invoke-Classify -Id $WindowId -Table $table
Save-Row $r
if ($r -and $Out)   { Write-Host ""; Write-Host "row appended -> $Out" }
if ($r -and $Label -and $r.truth) {
    Write-Host ""
    Write-Host ("labelled as {0} [{1}] -> {2}" -f $r.truth, $r.truth_src, $Calib) -ForegroundColor Green
    # A label is a statement about what is on screen. When most of the screen
    # is inside a surface, the features and the label describe different
    # things, and a fitter cannot learn the difference - it can only learn
    # noise. The row is kept, because the coverage is recorded with it and a
    # later pass can weight or drop it, but it must not pass silently.
    if ([int]$r.cover_opaque -ge 500) {
        Write-Host ""
        Write-Host ("  CAUTION    {0} permille of this screen is behind a surface." -f $r.cover_opaque) -ForegroundColor Yellow
        Write-Host  "  Your label describes what you SAW. The features describe the native" -ForegroundColor Yellow
        Write-Host  "  chrome around a region ArkUI cannot look into. If what you labelled" -ForegroundColor Yellow
        Write-Host  "  was drawn inside that region, this row teaches the fitter nothing." -ForegroundColor Yellow
        Write-Host  "  Kept, with cover_opaque recorded, so -Fit can set it aside." -ForegroundColor DarkGray
    }
    if ($r.scene -eq $r.truth) {
        Write-Host "  the classifier agreed. Still useful: it holds the answer in place while you tune." -ForegroundColor DarkGray
    } else {
        # A label on its own changes nothing, and it is worth saying why the
        # label was not taken - because the answer decides whether -Fit can
        # help at all, or whether the features are simply not there.
        Write-Host ("  the classifier said {0}." -f $r.scene) -ForegroundColor Yellow
        $got = $script:LastScores
        $want = 0.0
        if ($got -and $got.ContainsKey($r.truth)) { $want = $got[$r.truth] }
        Write-Host ("  {0} scored {1}; {2} scored {3}." -f $r.truth, [math]::Round($want,1), $r.scene, $r.score)
        if ($want -le 0) {
            Write-Host "  $($r.truth) scored NOTHING - no rule for it found any evidence at all." -ForegroundColor Red
            Write-Host "  -Fit cannot fix this. A threshold sweep only moves rules that already fire." -ForegroundColor Red
            Write-Host "  Run  scene_class.cmd -Window  and check the counts are really what is on screen." -ForegroundColor Red
        } else {
            Write-Host "  both rules fired, so this IS a threshold gap - exactly what -Fit tunes." -ForegroundColor DarkCyan
        }
    }
    Write-Host "when you have a dozen or so, run:  scene_class.cmd -Fit" -ForegroundColor DarkCyan
}
