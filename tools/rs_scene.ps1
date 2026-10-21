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
#
# The marker is a string this build happens to print, so it is a fast path and
# not the mechanism. The mechanism is that a node id cannot legitimately appear
# twice in one tree: the first repeat IS where the second pass begins, whatever
# the header above it says. If a build renames or drops the marker, the split
# still lands in the right place.
function Split-RsPasses {
    param([string[]] $Lines)

    $cut = -1
    $seen = @{}
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        $l = $Lines[$i]
        if ($cut -lt 0 -and $l -match 'RenderServiceUniThread') { $cut = $i; break }
        $m = [regex]::Match($l, '^\s*\|?\s*[A-Z_]+NODE\[(\d+)\]')
        if (-not $m.Success) { continue }
        $id = $m.Groups[1].Value
        if ($seen.ContainsKey($id)) { $cut = $i; break }
        $seen[$id] = $true
    }
    if ($cut -lt 0) { return [PSCustomObject]@{ Main = $Lines; Uni = @(); Split = "none" } }
    $how = "marker"
    if ($Lines[$cut] -notmatch 'RenderServiceUniThread') { $how = "repeated id" }
    return [PSCustomObject]@{
        Main  = $Lines[0..($cut-1)]
        Uni   = $Lines[$cut..($Lines.Count-1)]
        Split = $how
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

# Area of a set of rects, overlaps counted once.
#
# Was a grid scan: every cell of the compressed grid tested against every
# rect. That is fine for the handful of rects a window region holds and
# ruinous for the leaves of a dense screen - measured on device at 60ms for
# 35 rects, 5.5s for 200 and 24s for 300, per contributor, per sample.
#
# This sweeps instead. For each vertical slab between two x edges, collect the
# y intervals of the rects crossing it, merge them, and add width times merged
# height. Same answer, and the cost stops being the product of the two edge
# counts.
function Get-RegionArea {
    param([object[]] $Rects)
    if (-not $Rects -or $Rects.Count -eq 0) { return 0.0 }

    $rs = @()
    foreach ($r in $Rects) {
        if ($r.W -le 0 -or $r.H -le 0) { continue }
        $rs += [PSCustomObject]@{ X1=[double]$r.X; X2=([double]$r.X + $r.W)
                                  Y1=[double]$r.Y; Y2=([double]$r.Y + $r.H) }
    }
    if ($rs.Count -eq 0) { return 0.0 }

    $xs = @(@($rs | ForEach-Object { $_.X1; $_.X2 }) | Sort-Object -Unique)
    if ($xs.Count -lt 2) { return 0.0 }

    $total = 0.0
    for ($i = 0; $i -lt $xs.Count - 1; $i++) {
        $x1 = [double]$xs[$i]; $x2 = [double]$xs[$i+1]
        $w = $x2 - $x1
        if ($w -le 0) { continue }
        $mid = ($x1 + $x2) / 2.0

        $iv = @()
        foreach ($r in $rs) { if ($mid -ge $r.X1 -and $mid -lt $r.X2) { $iv += $r } }
        if ($iv.Count -eq 0) { continue }

        $iv = @($iv | Sort-Object Y1)
        $h = 0.0
        $cs = [double]$iv[0].Y1; $ce = [double]$iv[0].Y2
        for ($j = 1; $j -lt $iv.Count; $j++) {
            $a = [double]$iv[$j].Y1; $b = [double]$iv[$j].Y2
            if ($a -gt $ce) { $h += ($ce - $cs); $cs = $a; $ce = $b }
            elseif ($b -gt $ce) { $ce = $b }
        }
        $h += ($ce - $cs)
        $total += ($w * $h)
    }
    return $total
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
    $hwcIds = @{}

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
            # Named for what it is, not for what it sounds like. hasDrawContent
            # is 1 on 15 of 501 nodes, nearly all Text, SymbolGlyph and
            # TextClock, while every Image reads 0 - so it is not a statement
            # that the node has content. Best reading: it was re-recorded this
            # frame. Kept, reported, clustered on by nothing.
            Redrawn  = ($raw -match 'hasDrawContent: 1')
            Paints   = $false
            Slots    = @()
            Hwc      = $false
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
        # allHwcAndFilterNode is not a property of the node that prints it:
        # it is a list of OTHER nodes - the ones the system composes in
        # hardware or puts a filter on - and every ancestor repeats the list.
        # On this capture 74 nodes printed a list and the lists named 5
        # distinct ids between them, so counting the list per node counted
        # ancestors and called them composed layers.
        $hw = [regex]::Match($raw, 'allHwcAndFilterNode:\s*\{([^}]*)\}')
        if ($hw.Success -and $hw.Groups[1].Value.Trim()) {
            foreach ($h in [regex]::Matches($hw.Groups[1].Value, '<(\d+)>')) {
                $hwcIds[[long]$h.Groups[1].Value] = $true
            }
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
    foreach ($id in $hwcIds.Keys) { if ($byId.ContainsKey($id)) { $byId[$id].Hwc = $true } }
    foreach ($raw in $p.Uni) {
        $m = [regex]::Match($raw, '^\s*([A-Z_]+NODE)\[(\d+)\].*?DrawableVec:\[([^\]]*)\]')
        if (-not $m.Success) { continue }
        $id = [long]$m.Groups[2].Value
        if (-not $byId.ContainsKey($id)) { continue }
        $slots = @($m.Groups[3].Value -split ',' | Where-Object { $_.Trim() } | ForEach-Object { [int]$_.Trim() })
        $byId[$id].Drawables = $slots.Count
        $byId[$id].Slots     = $slots
        # Slot 28 is the content pass and slot 29 the children-only pass. Read
        # off this build: of 244 nodes carrying (24,29,31) every one is a
        # container - Stack, Row, __Common__ - and of the 40 carrying
        # (24,28,31) the tags are Image, SymbolGlyph, Canvas and Divider.
        # Every Image node has 28; not one has hasDrawContent.
        $byId[$id].Paints    = ($slots -contains 28)
    }

    return [PSCustomObject]@{
        Nodes = $nodes; Surfaces = $surfaces; ById = $byId
        VsyncId = $vsync; Animating = $animating; Split = $p.Split; HwcIds = $hwcIds
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
# A contributor is a WINDOW, not a surface. Those are not the same thing: a
# window is often a chain of surfaces, and only the outermost one carries the
# facts that matter.
#
#   SCBStatusBar25   vis=[0, 0, 1316, 203]   <- the window
#     StatusBarBox
#       ArkTSCardNode  vis=[Empty]           <- nested, holds the content
#
#   WindowScene_aweme215  <- SceneBoard's shell
#     aweme0              <- the app's own surface
#
# Measured on the home screen with the status bar plainly visible:
# VisibleRegion is populated for the window surface and reads Empty on the
# nested one. Treating the nested surface as its own contributor therefore
# deletes the status bar from the scene, and treating the shell and the app
# as two contributors counts one window twice.
#
# So: claim from the outermost surface down, through nested surfaces, and
# mark those nested so they never stand as contributors of their own.
function Set-RsOwnership {
    param($Tree)

    # Outermost first. A surface reached while claiming another surface's
    # subtree is by definition nested.
    $surfByDepth = @($Tree.Surfaces | Sort-Object Depth)
    foreach ($s in $surfByDepth) {
        Add-Member -InputObject $s -NotePropertyName Nested -NotePropertyValue $false -Force
    }
    $surfById = @{}
    foreach ($s in $Tree.Surfaces) { $surfById[$s.Id] = $s }

    foreach ($s in $surfByDepth) {
        if ($s.Nested) { continue }
        $seen = @{}
        $stack = New-Object System.Collections.Stack
        foreach ($k in $s.Node.Kids) { $stack.Push(@($k, $false)) }
        while ($stack.Count -gt 0) {
            $item = $stack.Pop()
            $id = [long]$item[0]; $skipCtx = [bool]$item[1]
            if ($seen.ContainsKey($id)) { continue }     # sortedChildren is
            $seen[$id] = $true                           # trusted to be a tree
            if (-not $Tree.ById.ContainsKey($id)) { continue }
            $n = $Tree.ById[$id]
            if ($n.Kind -eq 'SURFACE_NODE' -and $surfById.ContainsKey($id)) {
                $surfById[$id].Nested = $true
            }
            $n.SurfId = $s.Id
            $n.WinId  = $s.WinId
            # A node under a skipped subtree does not paint, and the dump does
            # not give it an absolute rect. Carrying the flag down is what
            # separates a node we correctly ignored from one we failed to read.
            Add-Member -InputObject $n -NotePropertyName SkipCtx -NotePropertyValue ($skipCtx -or $n.Skipped) -Force
            foreach ($k in $n.Kids) { $stack.Push(@($k, ($skipCtx -or $n.Skipped))) }
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
        $sh.paint_px = 0; $sh.drop_skip = 0; $sh.drop_odd = 0
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
    # 45 of the 46 rectless leaves on the capture this was built against were
    # under a skipped subtree - the renderer declined to draw them, and it is
    # right that they are not in the scene. Only the rest is evidence of a
    # parse we got wrong, so only the rest is worth raising.
    $sh.drop_skip = @($mine | Where-Object {
        (-not $_.Abs -or $_.Abs.W -le 0) -and ($_.PSObject.Properties['SkipCtx'] -and $_.SkipCtx) }).Count
    $sh.drop_odd  = @($mine | Where-Object {
        -not $_.Abs -and -not ($_.PSObject.Properties['SkipCtx'] -and $_.SkipCtx) -and
        $_.Local -and $_.Local.W -gt 0 }).Count
    if ($adm.Count -eq 0) { $sh.paint_px = 0; return [PSCustomObject]$sh }

    # Counts are per 1000 vp squared, not raw: raw counts do not survive a
    # device with a different panel, and the whole point of a class set is
    # that it transfers.
    $vp2 = ($own / ($Density * $Density)) / 1000.0
    if ($vp2 -le 0) { $vp2 = 1.0 }
    $sh.node_den = [math]::Round($adm.Count / $vp2, 2)

    $depths = @($adm | ForEach-Object { $_.Depth - $Surf.Depth })
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
    # Two rules this had wrong, both found by printing the winner next to a
    # screenshot of the screen it came from.
    #
    # It took the group with the most members, so on a video player it chose
    # four labels at 34x23 vp over the five-item action rail and the five-item
    # nav bar that dominate the screen. The group that matters is the one that
    # covers the most, not the one that counts the most: a feed is many cards
    # covering most of the display, and four glyphs are not a feed whatever
    # their count.
    #
    # And the floor was 24 raw pixels - 7 vp on this panel - which let glyph
    # fragments form groups at all. The runner-up on the same screen was four
    # leaves of 376x732 vp, i.e. four full-screen boxes stacked: that is
    # layering, not repetition, so near-full-screen members are excluded too.
    $minVp = 12.0
    $grp = @{}
    foreach ($n in $leaves) {
        $wv = $n.Abs.W / $Density; $hv = $n.Abs.H / $Density
        if ($wv -lt $minVp -or $hv -lt $minVp) { continue }
        if ($n.VisArea -gt (0.6 * $own)) { continue }
        $k = ("{0}x{1}" -f [int]([math]::Round($wv)), [int]([math]::Round($hv)))
        if (-not $grp.ContainsKey($k)) { $grp[$k] = @() }
        $grp[$k] += $n
    }
    $score = @{}
    foreach ($k in $grp.Keys) {
        if ($grp[$k].Count -lt 2) { continue }
        $a = 0.0
        foreach ($n in $grp[$k]) { $a += $n.VisArea }
        $score[$k] = $a
    }
    $best = $null
    foreach ($k in $score.Keys) {
        if ($null -eq $best -or $score[$k] -gt $score[$best]) { $best = $k }
    }
    $sh.repeat_n = 0; $sh.repeat_size = ""; $sh.repeat_frac = 0.0; $sh.repeat_2nd = ""
    if ($best -and $grp[$best].Count -gt 1) {
        $ra = 0.0
        foreach ($n in $grp[$best]) { $ra += $n.VisArea }
        $sh.repeat_n    = $grp[$best].Count
        $sh.repeat_size = $best
        $sh.repeat_frac = [math]::Round($ra / $own, 3)
        # The runner-up, because the winner alone cannot be checked against a
        # screenshot: a rail of five and a nav bar of five are both real, and
        # knowing only that the answer was "five" says nothing about which.
        $second = $null
        foreach ($k in $score.Keys) {
            if ($k -eq $best) { continue }
            if ($null -eq $second -or $score[$k] -gt $score[$second]) { $second = $k }
        }
        if ($second -and $grp[$second].Count -gt 1) {
            $sh.repeat_2nd = ("{0} x{1}" -f $second, $grp[$second].Count)
        }
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
    $sh.paints  = @($adm | Where-Object { $_.Paints }).Count
    $sh.redrawn = @($adm | Where-Object { $_.Redrawn }).Count
    $pa = 0.0
    foreach ($n in $adm) { if ($n.Paints) { $pa += $n.VisArea } }
    $sh.paint_frac = [math]::Round([math]::Min(1.0, $pa / $own), 3)
    # Hardware-composed or filtered: the biggest single lever in composition,
    # and worth an area rather than a count - one full-screen composed layer
    # and five small ones are not the same thing.
    $sh.hwc = @($adm | Where-Object { $_.Hwc }).Count
    $ha = 0.0
    foreach ($n in $adm) { if ($n.Hwc) { $ha += $n.VisArea } }
    $sh.hwc_frac = [math]::Round([math]::Min(1.0, $ha / $own), 3)
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

    # Painted area, and the one number that decides how much this contributor
    # counts in the scene. Not the region it spans: SCBGestureBack20 spans the
    # whole panel and draws three small things, and WindowScene_aweme215 spans
    # it and draws nothing at all - it is a host for the app's own surface.
    # Taking the span as weight gave those two the same say as the app.
    # Leaves only, because a container is a box, not a mark.
    $paint = @()
    foreach ($n in $leaves) {
        $x = [math]::Max($n.Abs.X, 0.0); $y = [math]::Max($n.Abs.Y, 0.0)
        $w = $n.Abs.W; $h = $n.Abs.H
        if ($w -le 0 -or $h -le 0) { continue }
        $paint += [PSCustomObject]@{ X=$x; Y=$y; W=$w; H=$h }
    }
    $sh.paint_px = [math]::Round((Get-RegionArea -Rects $paint), 0)

    # Animation, and what frame rate it asks for. This is the only place in any
    # dump we have found that states an expected frame rate.
    $an = @($adm | Where-Object { $_.Anim })
    $sh.anim_n = $an.Count
    $sh.anim_forever = @($an | Where-Object { $_.Anim.Repeat -lt 0 }).Count
    $sh.fps_pref = 0
    foreach ($n in $an) { if ($n.Anim.FpsPref -gt $sh.fps_pref) { $sh.fps_pref = $n.Anim.FpsPref } }
    # The platform's own name for what kind of scene this animation belongs
    # to, which is the one thing on the device that is doing our job already.
    # The value set is not discoverable: UNKNOWN_SCENE appears in no binary
    # under /system/lib64 on this build. So it is collected rather than
    # looked up, and the vocabulary accumulates across captures.
    $sh.fps_scene = ""
    foreach ($n in $an) {
        if ($n.Anim.Scene -and $n.Anim.Scene -ne 'UNKNOWN_SCENE') { $sh.fps_scene = $n.Anim.Scene; break }
        if (-not $sh.fps_scene) { $sh.fps_scene = $n.Anim.Scene }
    }

    return [PSCustomObject]$sh
}

# ----------------------------------------------------------- the display facts
#
# Read, not inferred. The panel used to be taken as the largest window rect,
# which an oversized window could redefine; DisplayManagerService states it.
function Get-RsDisplay {
    return (Read-RsDisplay -Lines (Invoke-HdcRaw "shell `"hidumper -s DisplayManagerService -a '-a'`""))
}

function Read-RsDisplay {
    param([string[]] $Lines)

    # The dump is sectioned, and the sections disagree on purpose:
    #
    #   [SCREEN PROPERTY]        RefreshRate: 30   <- what the panel is doing
    #   Client Screen Infos      RefreshRate: 60   <- what the mode allows
    #
    # Thirty is not an error. The only animation on screen asked for
    # FrameRateRange_prefered: 30 and the panel followed it down, which is the
    # whole mechanism this project exists to feed. Reading one value for
    # "refresh" threw that away and reported whichever section came last.
    #
    # It also opens with replayed log lines, timestamped, full of numbers that
    # match the same patterns as the fields below. Those are skipped.
    $d = [PSCustomObject]@{
        Id = 0; W = 0.0; H = 0.0; Density = 3.5; Rotation = 0
        Refresh = 0; RefreshMax = 0; Power = -1; Internal = $true; Extend = $false
        Cutout = $null; Avail = $null
    }
    $sect = ""
    foreach ($l in $Lines) {
        if ($l -match '^\s*\[\d\d-\d\d \d\d:\d\d:') { continue }   # replayed log
        if ($l -match '^\s*\[([A-Z][A-Z ]+)\]\s*$')        { $sect = $matches[1]; continue }
        if ($l -match '-+\s*Client Screen Infos\s*-+')        { $sect = "CLIENT"; continue }
        if ($l -match '-+\s*Display ID:\s*(\d+)\s*-+')      { $sect = "DISPLAY"; continue }

        if ($l -match '^\s*DisplayId:\s*(\d+)')              { $d.Id = [int]$matches[1] }
        if ($l -match '^\s*isInternal:\s*(\w+)')             { $d.Internal = ($matches[1] -eq 'true') }
        if ($l -match '^\s*isExtend:\s*(\w+)' -and $matches[1] -match 'true|1') { $d.Extend = $true }
        if ($l -match '^\s*ScreenPowerState:\s*(\d+)')       { $d.Power = [int]$matches[1] }
        if ($l -match '^\s*Bounds<L,T,W,H>:\s*(-?\d+),\s*(-?\d+),\s*(\d+),\s*(\d+)') {
            if ($d.W -le 0) { $d.W = [double]$matches[3]; $d.H = [double]$matches[4] }
        }
        if ($l -match '^\s*AvailableArea<X,Y,W,H>:?\s*(-?\d+),\s*(-?\d+),\s*(\d+),\s*(\d+)') {
            if (-not $d.Avail) {
                $d.Avail = [PSCustomObject]@{ X=[double]$matches[1]; Y=[double]$matches[2]
                                              W=[double]$matches[3]; H=[double]$matches[4] }
            }
        }
        if ($l -match '^\s*BoundingRects<X,Y,W,H>:\s*\[\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\s*\]') {
            $d.Cutout = [PSCustomObject]@{ X=[double]$matches[1]; Y=[double]$matches[2]
                                           W=[double]$matches[3]; H=[double]$matches[4] }
        }
        # activeModes<id, W, H, RS> states the mode's rate before any section
        # has had a chance to disagree about it.
        if ($l -match 'activeModes<[^>]*>:\s*\d+,\s*\d+,\s*\d+,\s*(\d+)') { $d.RefreshMax = [int]$matches[1] }

        if ($sect -eq 'SCREEN PROPERTY') {
            if ($l -match '^\s*Density:\s*([\d.]+)')    { $d.Density  = [double]$matches[1] }
            if ($l -match '^\s*Rotation:\s*(\d+)')      { $d.Rotation = [int]$matches[1] }
            if ($l -match '^\s*RefreshRate:\s*(\d+)')   { $d.Refresh  = [int]$matches[1] }
        }
        if ($sect -eq 'CLIENT' -and $l -match '^\s*RefreshRate:\s*(\d+)') {
            if ($d.RefreshMax -le 0) { $d.RefreshMax = [int]$matches[1] }
        }
    }
    if ($d.Refresh -le 0) { $d.Refresh = $d.RefreshMax }
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
    if ($Display.Avail -and $Display.Avail.W -gt 0) { $usable = $Display.Avail.W * $Display.Avail.H }
    if ($Display.Cutout) { $usable -= ($Display.Cutout.W * $Display.Cutout.H) }
    if ($usable -le 0) { $usable = $dw * $dh }

    # A surface with an empty visible region is not in the scene. The wallpaper
    # and the desktop behind a full-screen app drop out here, whole, with no
    # geometry computed at all.
    $live = @($Tree.Surfaces | Where-Object {
        -not $_.Nested -and $_.Visible -eq 1 -and $_.Alpha -gt 0.0 -and
        (Get-RegionArea -Rects $_.Vis) -gt 0
    })

    # Low zOrder is nearer the viewer on this build: the wallpaper sits at 108
    # with an empty visible region while the status bar sits at 9 and is on
    # screen. VisibleRegion is therefore already occlusion-resolved, which is
    # why admission needs no z walk of its own.
    $contrib = @()
    foreach ($s in ($live | Sort-Object { $_.Z })) {
        $sh = Measure-RsShape -Tree $Tree -Surf $s -Density $Display.Density
        $w = 0.0
        if ($sh.PSObject.Properties['paint_px']) { $w = [double]$sh.paint_px / $usable }
        Add-Member -InputObject $sh -NotePropertyName weight -NotePropertyValue ([math]::Round([math]::Min(1.0, $w), 4)) -Force
        # A surface that paints nothing is a host, not a contributor. The one
        # that matters is WindowScene_<app><id>: SceneBoard's shell around the
        # app's own surface. Counting it doubled the app's say in the scene.
        if ($sh.nodes -gt 0 -and $w -gt 0) { $contrib += $sh }
    }

    # Coverage from OpaqueRegion, never from the window rect. SCBGestureBack20
    # spans the whole panel and occludes nothing - its opaque region is empty -
    # and reading its rect as coverage is what made every content window report
    # zero unoccluded area.
    # Opaque regions come from every surface in a window's chain, not just the
    # one at the top of it. aweme0 carries the app's opaque region and is
    # nested inside WindowScene_aweme215, which carries none - collecting only
    # from contributors reported the display as entirely uncovered.
    $liveIds = @{}
    foreach ($c in $live) { $liveIds[$c.Id] = $true }
    $opq = @()
    foreach ($s in $Tree.Surfaces) {
        if ($liveIds.ContainsKey($s.Node.SurfId) -or $liveIds.ContainsKey($s.Id)) { $opq += $s.Opq }
    }
    $covered = Get-RegionArea -Rects $opq

    $sc = [ordered]@{
        vsync       = $Tree.VsyncId
        split       = $Tree.Split
        disp_w      = $dw
        disp_h      = $dh
        density     = $Display.Density
        rotation    = $Display.Rotation
        refresh     = $Display.Refresh
        refresh_max = $Display.RefreshMax
        power       = $Display.Power
        internal    = $Display.Internal
        contributors= $contrib.Count
        z_span      = 0
        # Coverage is against the panel, not against the usable area: an
        # opaque region spans the whole display including the cutout, and
        # dividing it by a denominator with the cutout removed reported 100.7%
        # of the screen as covered. Weights keep the usable denominator, which
        # is the area a contributor can actually compete for.
        cover_opaque= [math]::Round($covered / ($dw * $dh), 3)
        uncovered   = [math]::Round([math]::Max(0.0, 1.0 - ($covered / ($dw * $dh))), 3)
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
                     'corner_frac','blend_frac','dirty_frac','draw_slots','paint_frac','hwc_frac')) {
        $v = 0.0
        foreach ($c in $contrib) {
            $x = $c.PSObject.Properties[$f]
            if ($x -and $null -ne $x.Value) { $v += ([double]$x.Value * $c.weight) }
        }
        $sc[$f] = [math]::Round($v / $wsum, 3)
    }
    foreach ($f in @('nodes','dropped','drop_skip','drop_odd','paints','redrawn','repeat_n','scroller','surface_tag','hwc','hdr','anim_n','anim_forever','skipped')) {
        $v = 0
        foreach ($c in $contrib) {
            $x = $c.PSObject.Properties[$f]
            if ($x -and $null -ne $x.Value) { $v += [int]$x.Value }
        }
        $sc[$f] = $v
    }
    # How much of the tree actually made it into the scene. A node is dropped
    # when it has no innerAbsDrawRect, or when its rect falls outside what the
    # compositor says is visible. 52 of 485 nodes had no absolute rect on the
    # capture this was built against, and a scene computed from 60% of its
    # nodes used to be indistinguishable from one computed from all of them.
    $seen = [double]($sc.nodes + $sc.dropped)
    $sc.admit_frac = 1.0
    if ($seen -gt 0) { $sc.admit_frac = [math]::Round($sc.nodes / $seen, 3) }

    $sc.fps_pref = 0
    foreach ($c in $contrib) {
        $x = $c.PSObject.Properties['fps_pref']
        if ($x -and [int]$x.Value -gt $sc.fps_pref) { $sc.fps_pref = [int]$x.Value }
    }
    $sc.fps_scene = ""
    foreach ($c in $contrib) {
        $x = $c.PSObject.Properties['fps_scene']
        if ($x -and $x.Value -and $x.Value -ne 'UNKNOWN_SCENE') { $sc.fps_scene = $x.Value; break }
        if ($x -and $x.Value -and -not $sc.fps_scene) { $sc.fps_scene = $x.Value }
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
    Write-Host ("  display    {0}x{1} @{2}  rot {3}  {4}Hz of {5}  power {6}  vsync {7}" -f `
        $s.disp_w, $s.disp_h, $s.density, $s.rotation, $s.refresh, $s.refresh_max, $s.power, $s.vsync) -ForegroundColor DarkGray
    if ($s.split -ne "marker") {
        Write-Host ("  NOTE       pass split found by {0}, not by the usual marker" -f $s.split) -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host ("  {0,-26} {1,7} {2,7} {3,6} {4,7} {5,7} {6,7}" -f "contributor","weight","nodes","rep","text","image","dirty")
    foreach ($c in ($R.Contributors | Sort-Object { -$_.weight })) {
        $nm = $c.name; if ($nm.Length -gt 26) { $nm = $nm.Substring(0,26) }
        Write-Host ("  {0,-26} {1,7:N3} {2,7} {3,6} {4,7:N3} {5,7:N3} {6,7:N3}" -f `
            $nm, $c.weight, $c.nodes, $c.repeat_n, $c.text_frac, $c.image_frac, $c.dirty_frac)
    }
    Write-Host ""
    $admCol = "DarkGray"
    if ($s.drop_odd -gt 0) { $admCol = "Yellow" }
    Write-Host ("  contributors {0}   z span {1}   opaque cover {2}   uncovered {3}" -f `
        $s.contributors, $s.z_span, $s.cover_opaque, $s.uncovered)
    Write-Host ("  admitted     {0} of {1} ({2})   {3} skipped by the renderer, {4} unexplained" -f `
        $s.nodes, ($s.nodes + $s.dropped), $s.admit_frac, $s.drop_skip, $s.drop_odd) -ForegroundColor $admCol
    Write-Host ("  weighted     nodes/1000vp2 {0}   depth {1}   leaf {2}   fanout {3}" -f `
        $s.node_den, $s.depth_mean, $s.leaf_frac, $s.fanout)
    Write-Host ("  content      text {0}   image {1}   repeat {2}   scrollers {3}   surfaces {4}" -f `
        $s.text_frac, $s.image_frac, $s.repeat_frac, $s.scroller, $s.surface_tag)
    foreach ($c in ($R.Contributors | Sort-Object { -$_.weight } | Select-Object -First 3)) {
        if ($c.repeat_n -gt 1) {
            Write-Host ("  repeats      {0,-22} {1} x{2} vp{3}" -f $c.name, $c.repeat_size, $c.repeat_n,
                $(if ($c.repeat_2nd) { "   then " + $c.repeat_2nd } else { "" })) -ForegroundColor DarkGray
        }
    }
    Write-Host ("  content      paints {0} nodes over {1} of the display   ({2} re-recorded)" -f `
        $s.paints, $s.paint_frac, $s.redrawn)
    Write-Host ("  cost         blur {0}   shadow {1}   corner {2}   hwc area {3}   hdr {4}   slots {5}" -f `
        $s.blur_frac, $s.shadow_frac, $s.corner_frac, $s.hwc_frac, $s.hdr, $s.draw_slots)
    Write-Host ("  motion       dirty {0}   anims {1} ({2} endless)   prefers {3}fps [{5}]   skipped {4}" -f `
        $s.dirty_frac, $s.anim_n, $s.anim_forever, $s.fps_pref, $s.skipped, $s.fps_scene)
    if ($s.protected -gt 0) {
        Write-Host ("  PROTECTED    {0} layer(s) - content we are not allowed to see" -f $s.protected) -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "  shape is normalised inside each contributor; weight is its share of" -ForegroundColor DarkGray
    Write-Host "  the usable display. A split-screen chat and a full-screen one have the" -ForegroundColor DarkGray
    Write-Host "  same shape and different weight, which is the point." -ForegroundColor DarkGray
}

# Flatten the scene vector for the CSV. Prefixed rs_ so a row carries both
# routes side by side: the uitest features that the existing class set was
# measured on, and these. Nothing is replaced until the clustering says the
# new columns separate scenes better than the old ones, and that comparison
# is only possible if both are in the same row.
function Get-RsRowFields {
    param($R)
    $o = [ordered]@{}
    if (-not $R) { return $o }
    $s = $R.Scene
    foreach ($f in @('contributors','z_span','cover_opaque','uncovered','protected',
                     'node_den','depth_mean','leaf_frac','fanout','repeat_frac','repeat_n',
                     'text_frac','image_frac','sym_frac','blur_frac','shadow_frac',
                     'corner_frac','blend_frac','dirty_frac','draw_slots','nodes',
                     'scroller','surface_tag','hwc','hdr','anim_n','anim_forever',
                     'fps_pref','skipped','rotation','refresh','refresh_max','power','top_weight',
                     'dropped','admit_frac','drop_skip','drop_odd','paints','redrawn','paint_frac',
                     'hwc_frac','top_win')) {
        $p = $s.PSObject.Properties[$f]
        $o["rs_$f"] = if ($p -and $null -ne $p.Value) { $p.Value } else { 0 }
    }
    # Names are held out of clustering - they identify the app, and clustering
    # on the app is how you get one cluster per app instead of one per scene.
    $p = $s.PSObject.Properties['top_name']
    $o["rs_top_name"] = if ($p) { $p.Value } else { "" }
    $q = $s.PSObject.Properties['fps_scene']
    $o["rs_fps_scene"] = if ($q) { $q.Value } else { "" }
    $o["rs_vsync"]    = $s.vsync
    return $o
}

# The two RenderService dumps for one sample, saved raw. The join between them
# is only exact because nothing moves in between, so they are taken back to
# back and stored together or not at all.
function Save-RsSample {
    param([string] $Dir, [string] $Stem)
    $rs = @(Invoke-RsDump -Arg 'RSTree')
    $cl = @(Invoke-RsDump -Arg 'client')
    if ($rs.Count -lt 5) { return $null }
    if ($Dir -and $Stem) {
        $rs | Set-Content (Join-Path $Dir "$Stem.rstree.txt")
        $cl | Set-Content (Join-Path $Dir "$Stem.client.txt")
    }
    return [PSCustomObject]@{ Tree = (ConvertFrom-RsTree -Lines $rs); Map = (ConvertFrom-RsClient -Lines $cl) }
}
