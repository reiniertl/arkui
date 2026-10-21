# ---------------------------------------------------------------------------
#  rs_scene.ps1 - the scene, built from the render tree
#
#  Everything here reads RenderService. That is a different bargain from the
#  uitest path in scene_class.ps1, and the reason for the second path:
#
#    - it cannot be reshaped by the app. accessibilityGroup(true) collapses a
#      subtree out of uitest's view; it does not touch this one.
#    - it costs ~200ms per dump against uitest's ~1.4s, measured on device.
#    - it covers every window at once, so the scene stops being whatever the
#      foreground window happened to be.
#    - it carries occlusion as the compositor resolved it, not as we guessed.
#
#  What it does not carry is text. Nothing here will ever tell you a button
#  says "Send". If a feature needs that, it belongs on the uitest path.
#
#  Two dumps, joined by node id:
#    RSTree  - geometry, regions, effects, animations, dirty state
#    client  - frameNodeTag, so a render node can say what component it is
#  The join is exact (274/274 on the capture this was written against) because
#  both come from the same service microseconds apart. The join to uitest is
#  NOT exact - 38% tag agreement - so nothing here depends on it.
# ---------------------------------------------------------------------------

# RSTree prints the same tree twice: the main thread's view, which carries the
# properties, and then RenderServiceUniThread's, which carries shouldPaint_ and
# DrawableVec. Splitting on that marker is what keeps node counts honest -
# otherwise every node is counted twice.
function Split-RsPasses {
    param([string[]] $Lines)
    $cut = -1
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match 'RenderServiceUniThread') { $cut = $i; break }
    }
    if ($cut -lt 0) { return [PSCustomObject]@{ Main = $Lines; Uni = @() } }
    return [PSCustomObject]@{
        Main = $Lines[0..($cut-1)]
        Uni  = $Lines[$cut..($Lines.Count-1)]
    }
}

# A region is "[x, y, w, h]" repeated, or "[Empty]". Empty is not a parse
# failure - it is the compositor saying this surface contributes nothing, and
# it is the cheapest admission test we have.
function Get-RsRegion {
    param([string] $Line, [string] $Key)
    $m = [regex]::Match($Line, [regex]::Escape($Key) + '(?:\s+\d+)?\s*:?\s*(.*)$')
    if (-not $m.Success) { return @() }
    $rest = $m.Groups[1].Value
    $out = @()
    $rx = [regex]'^\s*\[\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*\]\s*,?'
    while ($true) {
        $r = $rx.Match($rest)
        if (-not $r.Success) { break }
        $out += [PSCustomObject]@{
            X = [double]$r.Groups[1].Value; Y = [double]$r.Groups[2].Value
            W = [double]$r.Groups[3].Value; H = [double]$r.Groups[4].Value
        }
        $rest = $rest.Substring($r.Length)
    }
    return ,$out
}

function Get-RsRectField {
    param([string] $Line, [string] $Key)
    $m = [regex]::Match($Line, [regex]::Escape($Key) + '\s*:?\s*\[\s*(-?[\d.]+)[,\s]+(-?[\d.]+)[,\s]+(-?[\d.]+)[,\s]+(-?[\d.]+)\s*\]')
    if (-not $m.Success) { return $null }
    return [PSCustomObject]@{
        X = [double]$m.Groups[1].Value; Y = [double]$m.Groups[2].Value
        W = [double]$m.Groups[3].Value; H = [double]$m.Groups[4].Value
    }
}

# Area of a set of rects, overlaps counted once. Coordinate compression, the
# same method Set-Occlusion uses on windows - a region here is usually one or
# two rects, so the grid stays tiny.
function Get-RegionArea {
    param([object[]] $Rects)
    if (-not $Rects -or $Rects.Count -eq 0) { return 0.0 }
    $xs = @(); $ys = @()
    foreach ($r in $Rects) {
        if ($r.W -le 0 -or $r.H -le 0) { continue }
        $xs += $r.X; $xs += ($r.X + $r.W)
        $ys += $r.Y; $ys += ($r.Y + $r.H)
    }
    $xs = @($xs | Sort-Object -Unique); $ys = @($ys | Sort-Object -Unique)
    if ($xs.Count -lt 2 -or $ys.Count -lt 2) { return 0.0 }
    $a = 0.0
    for ($i = 0; $i -lt $xs.Count - 1; $i++) {
        $cx = ([double]$xs[$i] + [double]$xs[$i+1]) / 2.0
        for ($j = 0; $j -lt $ys.Count - 1; $j++) {
            $cy = ([double]$ys[$j] + [double]$ys[$j+1]) / 2.0
            foreach ($r in $Rects) {
                if ($cx -ge $r.X -and $cx -lt ($r.X + $r.W) -and $cy -ge $r.Y -and $cy -lt ($r.Y + $r.H)) {
                    $a += ([double]$xs[$i+1] - [double]$xs[$i]) * ([double]$ys[$j+1] - [double]$ys[$j])
                    break
                }
            }
        }
    }
    return $a
}

function Get-RectOverlap {
    param($A, $B)
    if (-not $A -or -not $B) { return 0.0 }
    $l = [math]::Max([double]$A.X, [double]$B.X)
    $t = [math]::Max([double]$A.Y, [double]$B.Y)
    $r = [math]::Min([double]$A.X + $A.W, [double]$B.X + $B.W)
    $b = [math]::Min([double]$A.Y + $A.H, [double]$B.Y + $B.H)
    if ($r -le $l -or $b -le $t) { return 0.0 }
    return ($r - $l) * ($b - $t)
}

function Get-RectsOverlap {
    param($Rect, [object[]] $Region)
    $s = 0.0
    foreach ($g in $Region) { $s += (Get-RectOverlap -A $Rect -B $g) }
    return $s
}

# ---------------------------------------------------------------- the parser
#
# One record per node. Fields are pulled by name rather than by position
# because the set varies per node - a node with a shadow carries six shadow
# keys and a node without carries none - and because this build's vocabulary
# (216 distinct field names) is read off the dump, not documented. Anything
# not named here is still reachable through .Line.
function ConvertFrom-RsTree {
    param([string[]] $Lines)

    $p = Split-RsPasses -Lines $Lines
    $nodes = @(); $surfaces = @()
    $vsync = 0; $animating = @()

    foreach ($raw in $p.Main) {
        if (-not $raw) { continue }
        if ($raw -match '^\s*--\s*vsyncId:\s*(\d+)') { $vsync = [long]$matches[1]; continue }
        if ($raw -match '^\s*Animating Node:\s*\[([^\]]*)\]') {
            $animating = @($matches[1] -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
            continue
        }
        $m = [regex]::Match($raw, '^(\s*\|?\s*)([A-Z_]+NODE)\[(\d+)\]')
        if (-not $m.Success) { continue }

        $indent = $m.Groups[1].Value.Replace('|','').Length
        $id     = [long]$m.Groups[3].Value
        $kind   = $m.Groups[2].Value

        $n = [PSCustomObject]@{
            Id       = $id
            Pid      = [int]($id -shr 32)
            Kind     = $kind
            Depth    = [int]($indent / 2)
            Line     = $raw
            Abs      = (Get-RsRectField -Line $raw -Key 'innerAbsDrawRect')
            Local    = $null
            Tag      = ''          # filled from the client dump
            SurfId   = 0           # filled by Set-RsOwnership
            WinId    = 0
            Kids     = @()
            Name     = ''
            # effect and cost markers - sparse by nature, so they are counted
            # at the scene level and never read per node
            Shadow   = ($raw -match 'ShadowRadius|ShadowElevation')
            Blur     = ($raw -match 'BlurRadiusX|BackgroundFilter|HasFrostedGlassEffect|NeedFilter: true')
            Corner   = ($raw -match 'CornerRadius')
            Border   = ($raw -match 'BorderWidth')
            Blend    = ($raw -match 'blendType|skblendmode')
            Dirty    = ($raw -match 'isNodeDirty: 1|isSubTreeDirty: true')
            Skipped  = ($raw -match 'IsSubTreeSkipped: 1')
            Pure     = ($raw -match 'IsPureContainer: true')
            DrawsOwn = ($raw -match 'hasDrawContent: 1')
            Hwc      = 0
            Headroom = 1.0
            OldDirty = (Get-RsRectField -Line $raw -Key 'oldDirty')
            Anim     = $null
            Drawables = 0
        }

        $b = [regex]::Match($raw, 'Properties:\s*Bounds\[\s*(-?[\d.einf]+)\s+(-?[\d.einf]+)\s+(-?[\d.einf]+)\s+(-?[\d.einf]+)\s*\]')
        if ($b.Success -and $b.Groups[1].Value -notmatch 'inf') {
            $n.Local = [PSCustomObject]@{
                X = [double]$b.Groups[1].Value; Y = [double]$b.Groups[2].Value
                W = [double]$b.Groups[3].Value; H = [double]$b.Groups[4].Value
            }
        }
        if ($raw -match 'nodeName:\s*\[([^\]]*)\]') { $n.Name = $matches[1] }
        if ($raw -match 'headroom:\s*([\d.]+)')     { $n.Headroom = [double]$matches[1] }
        $hw = [regex]::Match($raw, 'allHwcAndFilterNode:\s*\{([^}]*)\}')
        if ($hw.Success -and $hw.Groups[1].Value.Trim()) {
            $n.Hwc = @([regex]::Matches($hw.Groups[1].Value, '<\d+>')).Count
        }
        $sc = [regex]::Match($raw, 'sortedChildren:\s*\d+\(([^)]*)\)')
        if ($sc.Success) {
            $n.Kids = @($sc.Groups[1].Value -split '\s+' | Where-Object { $_ -match '^\d+$' } | ForEach-Object { [long]$_ })
        }
        # An animation is the strongest single statement in the dump about what
        # this scene will cost over the next second: it declares a duration, a
        # repeat count, and the frame rate it wants.
        if ($raw -match 'RSAnimationManager') {
            $a = [PSCustomObject]@{ Type=''; Duration=0; Repeat=0; State=0; FpsMin=0; FpsMax=0; FpsPref=0; Scene='' }
            if ($raw -match 'Type:(\w+)')                        { $a.Type     = $matches[1] }
            if ($raw -match 'Duration:(\d+)')                    { $a.Duration = [int]$matches[1] }
            if ($raw -match 'RepeatCount:(-?\d+)')               { $a.Repeat   = [int]$matches[1] }
            if ($raw -match 'AnimationState:(\d+)')              { $a.State    = [int]$matches[1] }
            if ($raw -match 'FrameRateRange_min:(\d+)')          { $a.FpsMin   = [int]$matches[1] }
            if ($raw -match 'FrameRateRange_max:(\d+)')          { $a.FpsMax   = [int]$matches[1] }
            if ($raw -match 'FrameRateRange_prefered:(\d+)')     { $a.FpsPref  = [int]$matches[1] }
            if ($raw -match 'FrameRateRange_componentScene:(\w+)') { $a.Scene  = $matches[1] }
            $n.Anim = $a
        }

        if ($kind -eq 'SURFACE_NODE') {
            $s = [PSCustomObject]@{
                Id       = $id
                Pid      = $n.Pid
                Name     = ''
                WinId    = 0
                Z        = -1
                Alpha    = 1.0
                Visible  = 1
                Vis      = (Get-RsRegion -Line $raw -Key 'VisibleRegion')
                Opq      = (Get-RsRegion -Line $raw -Key 'OpaqueRegion')
                State    = ''
                Depth    = $n.Depth
                Special  = ($raw -match 'SpecialLayer: [1-9]')
                Node     = $n
            }
            if ($raw -match 'Name \[([^\]]*)\]')        { $s.Name  = $matches[1] }
            if ($raw -match 'zOrder:\s*(\d+)')          { $s.Z     = [int]$matches[1] }
            if ($raw -match 'Alpha:\s*([\d.]+)')        { $s.Alpha = [double]$matches[1] }
            if ($raw -match 'Visible:\s*(\d)')          { $s.Visible = [int]$matches[1] }
            if ($raw -match 'abilityState:\s*(\w+)')    { $s.State = $matches[1] }
            # SceneBoard names the host surface WindowScene_<app><winid>, and
            # the system panels SCBStatusBar25, SCBGestureNavBar15 and so on.
            # The trailing number is the window id WindowManagerService uses,
            # which is how a render node ends up attributable to a window.
            if ($s.Name -match '(\d+)$') { $s.WinId = [int]$matches[1] }
            $surfaces += $s
        }
        $nodes += $n
    }

    # The uni-render pass adds what the main pass does not: which drawable
    # slots a node actually has. A container carries three; a node with real
    # content carries six. It is the closest thing in the dump to "how much
    # work is this node" without looking at pixels.
    $byId = @{}
    foreach ($n in $nodes) { $byId[$n.Id] = $n }
    foreach ($raw in $p.Uni) {
        $m = [regex]::Match($raw, '^\s*([A-Z_]+NODE)\[(\d+)\].*?DrawableVec:\[([^\]]*)\]')
        if (-not $m.Success) { continue }
        $id = [long]$m.Groups[2].Value
        if (-not $byId.ContainsKey($id)) { continue }
        $byId[$id].Drawables = @($m.Groups[3].Value -split ',' | Where-Object { $_.Trim() }).Count
    }

    return [PSCustomObject]@{
        Nodes = $nodes; Surfaces = $surfaces; ById = $byId
        VsyncId = $vsync; Animating = $animating
    }
}

# The component tag. RSTree has no frameNodeId of its own, so this is the only
# route from a render node to what it is in ArkUI terms.
function ConvertFrom-RsClient {
    param([string[]] $Lines)
    $map = @{}
    foreach ($l in $Lines) {
        $m = [regex]::Match($l, '\w+Node\[(\d+)\].*?instanceId\[(\d+)\].*?frameNodeId\[(\d+)\], frameNodeTag\[([^\]]*)\]')
        if (-not $m.Success) { continue }
        $map[[long]$m.Groups[1].Value] = [PSCustomObject]@{
            Inst = $m.Groups[2].Value; FrameId = $m.Groups[3].Value; Tag = $m.Groups[4].Value
        }
    }
    return $map
}

# ------------------------------------------------------- nodes to contributors
#
# Every node belongs to exactly one window: the nearest SURFACE_NODE above it
# in the tree. Walking down from each surface and claiming its subtree does
# that in one pass, and the "nearest" part falls out of claiming in depth
# order - a nested surface overwrites its parent's claim on its own subtree.
function Set-RsOwnership {
    param($Tree)

    $surfByDepth = @($Tree.Surfaces | Sort-Object Depth)
    foreach ($s in $surfByDepth) {
        $stack = New-Object System.Collections.Stack
        foreach ($k in $s.Node.Kids) { $stack.Push($k) }
        while ($stack.Count -gt 0) {
            $id = $stack.Pop()
            if (-not $Tree.ById.ContainsKey($id)) { continue }
            $n = $Tree.ById[$id]
            # Do not cross into another surface: that subtree is its own
            # contributor and will be claimed when its surface comes up.
            if ($n.Kind -eq 'SURFACE_NODE') { continue }
            $n.SurfId = $s.Id
            $n.WinId  = $s.WinId
            foreach ($k in $n.Kids) { $stack.Push($k) }
        }
        $s.Node.SurfId = $s.Id
        $s.Node.WinId  = $s.WinId
    }
}

function Set-RsTags {
    param($Tree, $ClientMap)
    if (-not $ClientMap) { return }
    foreach ($n in $Tree.Nodes) {
        if ($ClientMap.ContainsKey($n.Id)) { $n.Tag = $ClientMap[$n.Id].Tag }
    }
}

# ------------------------------------------------------------ the shape vector
#
# Per contributor, normalised by that contributor's OWN visible area. This is
# the decision that makes the scene display-scoped without making it
# resolution-scoped: a chat in a half-height split window has rows half as
# tall as a full-screen chat, but the same proportions, the same repeat count
# and the same text-to-image ratio. Divided by its own area it reads as the
# same shape - which is what it is. Divided by the display it would read as a
# different scene, which is the mistake this avoids.
#
# How much the contributor MATTERS to the scene is a separate number, carried
# alongside as Weight and applied only when the display vector is composed.
function Measure-RsShape {
    param($Tree, $Surf, [double] $Density = 3.5)

    $own = Get-RegionArea -Rects $Surf.Vis
    $sh = [ordered]@{
        win = $Surf.WinId; name = $Surf.Name; pid = $Surf.Pid; z = $Surf.Z
        state = $Surf.State; alpha = [math]::Round($Surf.Alpha, 3)
        vis_px = [math]::Round($own, 0)
    }
    $mine = @($Tree.Nodes | Where-Object { $_.SurfId -eq $Surf.Id -and $_.Kind -ne 'SURFACE_NODE' })
    if ($own -le 0 -or $mine.Count -eq 0) {
        $sh.nodes = $mine.Count
        return [PSCustomObject]$sh
    }

    # Admitted = has a real rect and lands inside what the compositor says is
    # visible. A node outside the visible region is not in the scene no matter
    # what the tree says about it.
    $adm = @()
    foreach ($n in $mine) {
        if (-not $n.Abs -or $n.Abs.W -le 0 -or $n.Abs.H -le 0) { continue }
        $ov = Get-RectsOverlap -Rect $n.Abs -Region $Surf.Vis
        if ($ov -le 0) { continue }
        Add-Member -InputObject $n -NotePropertyName VisArea -NotePropertyValue $ov -Force
        $adm += $n
    }
    $sh.nodes    = $adm.Count
    $sh.dropped  = $mine.Count - $adm.Count
    if ($adm.Count -eq 0) { return [PSCustomObject]$sh }

    # Counts are per 1000 vp squared, not raw: raw counts do not survive a
    # device with a different panel, and the whole point of a class set is
    # that it transfers.
    $vp2 = ($own / ($Density * $Density)) / 1000.0
    if ($vp2 -le 0) { $vp2 = 1.0 }
    $sh.node_den = [math]::Round($adm.Count / $vp2, 2)

    $depths = @($adm | ForEach-Object { $_.Depth })
    $sh.depth_max  = ($depths | Measure-Object -Maximum).Maximum
    $sh.depth_mean = [math]::Round((($depths | Measure-Object -Average).Average), 2)
    $leaves = @($adm | Where-Object { $_.Kids.Count -eq 0 })
    $sh.leaf_n     = $leaves.Count
    $sh.leaf_frac  = [math]::Round($leaves.Count / [double]$adm.Count, 3)
    $kidc = @($adm | Where-Object { $_.Kids.Count -gt 0 } | ForEach-Object { $_.Kids.Count })
    $sh.fanout = 0.0
    if ($kidc.Count -gt 0) { $sh.fanout = [math]::Round((($kidc | Measure-Object -Average).Average), 2) }

    # Repeats are the single strongest structural signal we have: a gallery, a
    # feed and an icon grid differ from a player and a form mainly in how many
    # same-sized things they show. Sized in vp so the group survives scaling.
    $grp = @{}
    foreach ($n in $leaves) {
        if ($n.Abs.W -lt 24 -or $n.Abs.H -lt 24) { continue }
        $k = ("{0}x{1}" -f [int]([math]::Round($n.Abs.W / $Density)), [int]([math]::Round($n.Abs.H / $Density)))
        if (-not $grp.ContainsKey($k)) { $grp[$k] = @() }
        $grp[$k] += $n
    }
    $best = $null
    foreach ($k in $grp.Keys) {
        if ($null -eq $best -or $grp[$k].Count -gt $grp[$best].Count) { $best = $k }
    }
    $sh.repeat_n = 0; $sh.repeat_size = ""; $sh.repeat_frac = 0.0
    if ($best -and $grp[$best].Count -gt 1) {
        $ra = 0.0
        foreach ($n in $grp[$best]) { $ra += $n.VisArea }
        $sh.repeat_n    = $grp[$best].Count
        $sh.repeat_size = $best
        $sh.repeat_frac = [math]::Round($ra / $own, 3)
    }

    # Area by component kind. Area, not count, because one full-bleed Image is
    # a gallery and forty tiny ones are a grid, and counting cannot tell them
    # apart.
    foreach ($pair in @(@('text','Text'), @('image','Image'), @('sym','SymbolGlyph'))) {
        $a = 0.0
        foreach ($n in $adm) { if ($n.Tag -eq $pair[1]) { $a += $n.VisArea } }
        $sh[($pair[0] + "_frac")] = [math]::Round($a / $own, 3)
    }
    $sh.tagged = @($adm | Where-Object { $_.Tag }).Count
    $container = @('List','Grid','Swiper','Scroll','Tabs','WaterFlow','Navigation')
    $sh.scroller = @($adm | Where-Object { $container -contains $_.Tag }).Count
    $sh.surface_tag = @($adm | Where-Object { $_.Tag -match 'XComponent|Web|UIExtension|Embedded|Plugin' }).Count

    # Effect load. Sparse per node - blur sat on 5 of 506 nodes on the capture
    # this was written against - so these are only ever read as a fraction of
    # the visible area, never as a per-node feature.
    foreach ($pair in @(@('blur','Blur'), @('shadow','Shadow'), @('corner','Corner'), @('blend','Blend'))) {
        $a = 0.0
        foreach ($n in $adm) { if ($n.($pair[1])) { $a += $n.VisArea } }
        $sh[($pair[0] + "_frac")] = [math]::Round([math]::Min(1.0, $a / $own), 3)
    }
    $sh.draws_own = @($adm | Where-Object { $_.DrawsOwn }).Count
    $sh.hwc       = @($adm | Where-Object { $_.Hwc -gt 0 }).Count
    $sh.hdr       = @($adm | Where-Object { $_.Headroom -gt 1.0 }).Count
    $dv = @($adm | Where-Object { $_.Drawables -gt 0 } | ForEach-Object { $_.Drawables })
    $sh.draw_slots = 0.0
    if ($dv.Count -gt 0) { $sh.draw_slots = [math]::Round((($dv | Measure-Object -Average).Average), 2) }

    # Change, from one read. oldDirty is a rect per node, so how much of this
    # contributor is repainting is answerable without a previous sample. The
    # delta against the previous sample is a different and still useful thing;
    # this is not a replacement for it.
    $dirty = @($adm | Where-Object { $_.OldDirty -and $_.OldDirty.W -gt 0 } | ForEach-Object { $_.OldDirty })
    $sh.dirty_frac = 0.0
    if ($dirty.Count -gt 0) {
        $sh.dirty_frac = [math]::Round([math]::Min(1.0, (Get-RegionArea -Rects $dirty) / $own), 3)
    }
    $sh.skipped = @($adm | Where-Object { $_.Skipped }).Count

    # Animation, and what frame rate it asks for. This is the only place in any
    # dump we have found that states an expected frame rate.
    $an = @($adm | Where-Object { $_.Anim })
    $sh.anim_n = $an.Count
    $sh.anim_forever = @($an | Where-Object { $_.Anim.Repeat -lt 0 }).Count
    $sh.fps_pref = 0
    foreach ($n in $an) { if ($n.Anim.FpsPref -gt $sh.fps_pref) { $sh.fps_pref = $n.Anim.FpsPref } }

    return [PSCustomObject]$sh
}

# ----------------------------------------------------------- the display facts
#
# Read, not inferred. The panel used to be taken as the largest window rect,
# which an oversized window could redefine; DisplayManagerService states it.
function Get-RsDisplay {
    $lines = Invoke-HdcRaw "shell `"hidumper -s DisplayManagerService -a '-a'`""
    $d = [PSCustomObject]@{
        Id = 0; W = 0.0; H = 0.0; Density = 3.5; Rotation = 0
        Refresh = 0; Power = -1; Internal = $true; Cutout = $null
    }
    foreach ($l in $lines) {
        if ($l -match 'DisplayId\s*[:=]?\s*(\d+)')               { $d.Id = [int]$matches[1] }
        if ($l -match 'Bounds[^\d-]*(-?\d+)[,\s]+(-?\d+)[,\s]+(\d+)[,\s]+(\d+)') {
            if ($d.W -le 0) { $d.W = [double]$matches[3]; $d.H = [double]$matches[4] }
        }
        if ($l -match 'Density\s*[:=]?\s*([\d.]+)')               { $d.Density  = [double]$matches[1] }
        if ($l -match 'Rotation\s*[:=]?\s*(\d+)')                 { $d.Rotation = [int]$matches[1] }
        if ($l -match 'RefreshRate\s*[:=]?\s*(\d+)')              { $d.Refresh  = [int]$matches[1] }
        if ($l -match 'ScreenPowerState\s*[:=]?\s*(\d+)')         { $d.Power    = [int]$matches[1] }
        if ($l -match 'isInternal\s*[:=]?\s*(\w+)')               { $d.Internal = ($matches[1] -eq 'true') }
        if ($l -match 'Cutout.*?\[\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)[,\s]+(\d+)\s*\]') {
            $d.Cutout = [PSCustomObject]@{
                X=[double]$matches[1]; Y=[double]$matches[2]; W=[double]$matches[3]; H=[double]$matches[4] }
        }
    }
    return $d
}

# ------------------------------------------------------------ the scene vector
#
#   scene = SUM over contributors of (shape_i * weight_i)  +  display facts
#
# Shape is normalised inside the contributor; weight is the contributor's
# share of the usable display. A 5% floating window therefore cannot drag the
# class, and a full-screen one dominates it, which is what the eye does too.
#
# Display facts are the things no window owns: how many contributors there
# are, how deep the stack is, how much of the panel is covered at all.
function Get-RsScene {
    param($Tree, $ClientMap, $Display)

    Set-RsOwnership -Tree $Tree
    Set-RsTags -Tree $Tree -ClientMap $ClientMap

    $dw = $Display.W; $dh = $Display.H
    if ($dw -le 0 -or $dh -le 0) { $dw = 1.0; $dh = 1.0 }
    $usable = $dw * $dh
    if ($Display.Cutout) { $usable -= ($Display.Cutout.W * $Display.Cutout.H) }
    if ($usable -le 0) { $usable = $dw * $dh }

    # A surface with an empty visible region is not in the scene. The wallpaper
    # and the desktop behind a full-screen app drop out here, whole, with no
    # geometry computed at all.
    $live = @($Tree.Surfaces | Where-Object {
        $_.Visible -eq 1 -and $_.Alpha -gt 0.0 -and (Get-RegionArea -Rects $_.Vis) -gt 0
    })

    $contrib = @()
    foreach ($s in ($live | Sort-Object { -$_.Z })) {
        $sh = Measure-RsShape -Tree $Tree -Surf $s -Density $Display.Density
        Add-Member -InputObject $sh -NotePropertyName weight -NotePropertyValue ([math]::Round($sh.vis_px / $usable, 4)) -Force
        $contrib += $sh
    }

    # Coverage from OpaqueRegion, never from the window rect. SCBGestureBack20
    # spans the whole panel and occludes nothing - its opaque region is empty -
    # and reading its rect as coverage is what made every content window report
    # zero unoccluded area.
    $opq = @()
    foreach ($s in $live) { $opq += $s.Opq }
    $covered = Get-RegionArea -Rects $opq

    $sc = [ordered]@{
        vsync       = $Tree.VsyncId
        disp_w      = $dw
        disp_h      = $dh
        density     = $Display.Density
        rotation    = $Display.Rotation
        refresh     = $Display.Refresh
        power       = $Display.Power
        internal    = $Display.Internal
        contributors= $contrib.Count
        z_span      = 0
        cover_opaque= [math]::Round($covered / $usable, 3)
        uncovered   = [math]::Round([math]::Max(0.0, 1.0 - ($covered / $usable)), 3)
        protected   = @($live | Where-Object { $_.Special }).Count
        anim_live   = $Tree.Animating.Count
    }
    if ($contrib.Count -gt 0) {
        $zs = @($live | ForEach-Object { $_.Z } | Where-Object { $_ -ge 0 })
        if ($zs.Count -gt 0) {
            $sc.z_span = ([int](($zs | Measure-Object -Maximum).Maximum) - [int](($zs | Measure-Object -Minimum).Minimum))
        }
    }

    # The weighted roll-up. Only the shape numbers are weighted; counts that
    # are inherently scene-wide (animations, protected layers) are summed, and
    # a frame rate is a maximum because the fastest thing on screen sets the
    # floor for everything.
    $wsum = 0.0
    foreach ($c in $contrib) { $wsum += $c.weight }
    if ($wsum -le 0) { $wsum = 1.0 }
    foreach ($f in @('node_den','depth_mean','leaf_frac','fanout','repeat_frac',
                     'text_frac','image_frac','sym_frac','blur_frac','shadow_frac',
                     'corner_frac','blend_frac','dirty_frac','draw_slots')) {
        $v = 0.0
        foreach ($c in $contrib) {
            $x = $c.PSObject.Properties[$f]
            if ($x -and $null -ne $x.Value) { $v += ([double]$x.Value * $c.weight) }
        }
        $sc[$f] = [math]::Round($v / $wsum, 3)
    }
    foreach ($f in @('nodes','repeat_n','scroller','surface_tag','hwc','hdr','anim_n','anim_forever','skipped')) {
        $v = 0
        foreach ($c in $contrib) {
            $x = $c.PSObject.Properties[$f]
            if ($x -and $null -ne $x.Value) { $v += [int]$x.Value }
        }
        $sc[$f] = $v
    }
    $sc.fps_pref = 0
    foreach ($c in $contrib) {
        $x = $c.PSObject.Properties['fps_pref']
        if ($x -and [int]$x.Value -gt $sc.fps_pref) { $sc.fps_pref = [int]$x.Value }
    }
    # The dominant contributor, kept because the roll-up alone cannot say
    # whether one window owns the scene or five share it.
    $top = $contrib | Sort-Object { -$_.weight } | Select-Object -First 1
    if ($top) {
        $sc.top_win    = $top.win
        $sc.top_name   = $top.name
        $sc.top_weight = $top.weight
    }

    return [PSCustomObject]@{ Scene = [PSCustomObject]$sc; Contributors = $contrib; Display = $Display }
}

# One call, three dumps, one scene. Kept together because the two RenderService
# dumps must be taken as close together as possible - their join is exact only
# because nothing moves between them.
function Get-RsSceneNow {
    $rs = @(Invoke-RsDump -Arg 'RSTree')
    $cl = @(Invoke-RsDump -Arg 'client')
    $tree = ConvertFrom-RsTree -Lines $rs
    $map  = ConvertFrom-RsClient -Lines $cl
    $disp = Get-RsDisplay
    return (Get-RsScene -Tree $tree -ClientMap $map -Display $disp)
}

function Show-RsScene {
    param($R)
    $s = $R.Scene
    Write-Host ""
    Write-Host "SCENE FROM THE RENDER TREE  [RS]" -ForegroundColor Cyan
    Write-Host ("  display    {0}x{1} @{2}  rot {3}  {4}Hz  power {5}  vsync {6}" -f `
        $s.disp_w, $s.disp_h, $s.density, $s.rotation, $s.refresh, $s.power, $s.vsync) -ForegroundColor DarkGray
    Write-Host ""
    Write-Host ("  {0,-26} {1,>7} {2,>7} {3,>6} {4,>7} {5,>7} {6,>7}" -f "contributor","weight","nodes","rep","text","image","dirty")
    foreach ($c in ($R.Contributors | Sort-Object { -$_.weight })) {
        $nm = $c.name; if ($nm.Length -gt 26) { $nm = $nm.Substring(0,26) }
        Write-Host ("  {0,-26} {1,7:N3} {2,7} {3,6} {4,7:N3} {5,7:N3} {6,7:N3}" -f `
            $nm, $c.weight, $c.nodes, $c.repeat_n, $c.text_frac, $c.image_frac, $c.dirty_frac)
    }
    Write-Host ""
    Write-Host ("  contributors {0}   z span {1}   opaque cover {2}   uncovered {3}" -f `
        $s.contributors, $s.z_span, $s.cover_opaque, $s.uncovered)
    Write-Host ("  weighted     nodes/1000vp2 {0}   depth {1}   leaf {2}   fanout {3}" -f `
        $s.node_den, $s.depth_mean, $s.leaf_frac, $s.fanout)
    Write-Host ("  content      text {0}   image {1}   repeat {2}   scrollers {3}   surfaces {4}" -f `
        $s.text_frac, $s.image_frac, $s.repeat_frac, $s.scroller, $s.surface_tag)
    Write-Host ("  cost         blur {0}   shadow {1}   corner {2}   hwc {3}   hdr {4}   slots {5}" -f `
        $s.blur_frac, $s.shadow_frac, $s.corner_frac, $s.hwc, $s.hdr, $s.draw_slots)
    Write-Host ("  motion       dirty {0}   anims {1} ({2} endless)   prefers {3}fps   skipped {4}" -f `
        $s.dirty_frac, $s.anim_n, $s.anim_forever, $s.fps_pref, $s.skipped)
    if ($s.protected -gt 0) {
        Write-Host ("  PROTECTED    {0} layer(s) - content we are not allowed to see" -f $s.protected) -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "  shape is normalised inside each contributor; weight is its share of" -ForegroundColor DarkGray
    Write-Host "  the usable display. A split-screen chat and a full-screen one have the" -ForegroundColor DarkGray
    Write-Host "  same shape and different weight, which is the point." -ForegroundColor DarkGray
}
