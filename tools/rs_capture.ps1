# ---------------------------------------------------------------------------
#  rs_capture.ps1 - pull RenderService dumps off the device. Nothing else.
#
#  This is the whole of the device side. It drives hdc, decides from the raw
#  text whether a scene has already been seen, and writes what it pulled:
#
#      <session>/<nnnn>.rstree.txt    the render tree, per distinct scene
#      <session>/<nnnn>.client.txt    frameNodeTag, so a node can be named
#      <session>/<nnnn>.jpeg          a picture of that scene
#      <session>/raw/<nnnn>.rstree.txt   every sample, deduplicated or not
#      <session>/samples.csv          the index: raw, stem, sig, shot
#      display.txt                    geometry, density, rotation, cutout
#
#  It computes no features and names no scenes. Everything downstream of the
#  files above is Python: tools/cluster/rs_pattern.py reads them directly.
#
#  It used to be two scripts totalling 6,850 lines, because both of them also
#  carried a feature extractor and a hand-tuned classifier over 23 scene
#  classes. Those were a second implementation of work the Python already did,
#  and the classes were asserted rather than measured. Both are gone; this is
#  what was actually irreducible.
#
#  Two dumps, joined by node id:
#    RSTree  - geometry, regions, effects, animations, dirty state
#    client  - frameNodeTag, so a render node can say what component it is
#  The join is exact because both come from the same service microseconds
#  apart.
#
#      .\rs_capture.ps1 -Seconds 600 -Dir C:\ark_ui\captures
# ---------------------------------------------------------------------------

[CmdletBinding()]
param(
    [int]    $Seconds   = 300,
    [string] $Dir       = "captures",
    [int]    $Tol       = 650,     # permille of rects shared before two
                                   # samples count as the same scene
    [int]    $ShotEvery = 5,       # take a picture anyway after this many
                                   # samples on one scene that never settles
    [switch] $ShowCmd              # echo every hdc command
)

$ErrorActionPreference = "Stop"

# --- the device ------------------------------------------------------------

function Invoke-HdcRaw {
    param([string] $CommandLine)
    $full = "hdc $CommandLine"
    if ($ShowCmd) { Write-Host "  > $full" -ForegroundColor DarkGray }
    $out = & cmd.exe /c $full 2>&1
    return ($out | ForEach-Object { "$_" })
}

function Invoke-RsDump {
    param([string] $Arg)
    return Invoke-HdcRaw "shell `"hidumper -s RenderService -a '$Arg'`""
}

function Get-DeviceFileSize {
    param([string] $Path)
    $resp = Invoke-HdcRaw "shell `"wc -c < $Path 2>/dev/null`""
    foreach ($l in $resp) {
        $m = [regex]::Match("$l", '^\s*(\d+)')
        if ($m.Success) { return [long]$m.Groups[1].Value }
    }
    return 0
}

function Find-ShotCommand {
    # Each candidate names its own device file, because the suffix is not a
    # detail: snapshot_display refuses anything but .jpeg - "error: fileName
    # ... invalid, suffix must be .jpeg" - and probing it with a .png was why
    # it was written off as unavailable and every capture fell through to
    # uitest screenCap. It works, it needs no uitest, and a frame is 187 KB
    # against the PNG's 3.5 MB.
    $cands = @(
        @{ Name = "snapshot_display -f";   Dev = "/data/local/tmp/ark_shot.jpeg"; Bin = "snapshot_display -f"
           Line = "shell `"snapshot_display -f /data/local/tmp/ark_shot.jpeg`"" },
        @{ Name = "snapshot_display -i 0"; Dev = "/data/local/tmp/ark_shot.jpeg"; Bin = "snapshot_display -i 0 -f"
           Line = "shell `"snapshot_display -i 0 -f /data/local/tmp/ark_shot.jpeg`"" },
        @{ Name = "screencap -p";          Dev = "/data/local/tmp/ark_shot.png"; Bin = "screencap -p"
           Line = "shell `"screencap -p /data/local/tmp/ark_shot.png`"" },
        # Last, and only as a fallback: it is the uitest binary. A capture
        # that reads no layout tree still shells out to uitest for a picture
        # if nothing above answers, and that is worth knowing rather than
        # discovering later.
        @{ Name = "uitest screenCap -p";   Dev = "/data/local/tmp/ark_shot.png"; Bin = "uitest screenCap -p"
           Line = "shell `"uitest screenCap -p /data/local/tmp/ark_shot.png`"" }
    )
    Write-Host "probing for a screenshot command..." -ForegroundColor Cyan
    foreach ($c in $cands) {
        Invoke-HdcRaw "shell `"rm -f $($c.Dev)`"" | Out-Null
        $resp = Invoke-HdcRaw $c.Line
        $sz = Get-DeviceFileSize $c.Dev
        if ($sz -gt 10000) {
            Write-Host ("  {0,-22} OK  ({1:N0} bytes, {2})" -f $c.Name, $sz,
                [System.IO.Path]::GetExtension($c.Dev)) -ForegroundColor Green
            if ($c.Name -like "uitest*") {
                Write-Host "  NOTE  this is the uitest binary. No layout tree is read or stored," -ForegroundColor Yellow
                Write-Host "        but the picture comes from uitest, not from the render path." -ForegroundColor Yellow
            }
            $script:ShotCmd  = $c.Line
            $script:ShotName = $c.Name
            $script:ShotDev  = $c.Dev
            $script:ShotExt  = [System.IO.Path]::GetExtension($c.Dev)
            $script:ShotBin  = $c.Bin
            return $true
        }
        $why = if ($sz -gt 0) { "$sz bytes - too small to be a frame" }
               elseif ($resp -and ($resp -join " ").Trim()) { ($resp -join " ").Trim() }
               else { "no file" }
        if ($why.Length -gt 60) { $why = $why.Substring(0, 60) }
        Write-Host ("  {0,-22} no  ({1})" -f $c.Name, $why) -ForegroundColor DarkGray
    }
    Write-Host ""
    Write-Host "No screenshot command answered. Capture needs one - the whole point is" -ForegroundColor Red
    Write-Host "the picture beside the dump. Find what this build calls it:" -ForegroundColor Red
    Write-Host "  hdc shell ls /system/bin | findstr /i snap" -ForegroundColor Yellow
    Write-Host "  hdc shell ls /system/bin | findstr /i screen" -ForegroundColor Yellow
    Write-Host "and tell me the name; it is one line to add." -ForegroundColor Yellow
    return $false
}

# How alike two sets of rects are, in permille. The capture's only test for
# 'this is still the same scene'.
function Get-SetHold {
    param($A, $B)
    if ($A.Count -eq 0 -and $B.Count -eq 0) { return 1000 }
    if ($A.Count -eq 0 -or  $B.Count -eq 0) { return 0 }
    $inter = 0
    foreach ($k in $A) { if ($B.Contains($k)) { $inter++ } }
    $union = $A.Count + $B.Count - $inter
    if ($union -le 0) { return 1000 }
    return [int][math]::Round(1000.0 * $inter / $union, [System.MidpointRounding]::AwayFromZero)
}

# --- the scene signature ---------------------------------------------------


# A signature straight off the dump text, with no tree and no scene built.
#
# The capture loop needs two readings per sample: one to describe the scene
# and one, after the screenshot, to say whether anything moved while the
# picture was taken. Only the first is a scene. The second was being produced
# by running the entire construction again - parse, ownership walk,
# per-contributor shape, region sweeps - about 1.3s to answer a yes/no
# question about rects.
#
# A scene is one read, processed once. This is the other thing: one regex
# pass over the raw lines for the absolute rects and the surface names, which
# is all the comparison ever looks at.
function Get-RsQuickSig {
    param([string[]] $Lines)

    $all = New-Object 'System.Collections.Generic.HashSet[string]'
    $big = New-Object 'System.Collections.Generic.HashSet[string]'
    if (-not $Lines) { return [PSCustomObject]@{ Hash = ""; Big = $big; All = $all } }

    $g = [double]$script:SigGrid
    if ($g -lt 1) { $g = 16.0 }
    $floor = [double]$script:SigBigArea
    if ($floor -le 0) { $floor = 20.0 }
    $dispArea = 0.0
    if ($script:ScreenW -gt 0) { $dispArea = [double]$script:ScreenW * [double]$script:ScreenH }

    $rx   = [regex]'innerAbsDrawRect: \[\s*(-?\d+),\s*(-?\d+),\s*(-?\d+),\s*(-?\d+)\s*\]'
    $rxNm = [regex]'Name \[([^\]]+)\]'
    foreach ($l in $Lines) {
        # Contains, not -like: '[' opens a character class in a wildcard
        # pattern, and '*NODE[*' is not a valid one.
        if (-not $l.Contains('NODE[')) { continue }
        $m = $rx.Match($l)
        if ($m.Success) {
            $x = [double]$m.Groups[1].Value; $y = [double]$m.Groups[2].Value
            $w = [double]$m.Groups[3].Value; $h = [double]$m.Groups[4].Value
            if ($w -gt 0 -and $h -gt 0) {
                $k = "{0},{1},{2},{3}" -f `
                    ([int][math]::Round($x / $g, [System.MidpointRounding]::AwayFromZero)),
                    ([int][math]::Round($y / $g, [System.MidpointRounding]::AwayFromZero)),
                    ([int][math]::Round(($x + $w) / $g, [System.MidpointRounding]::AwayFromZero)),
                    ([int][math]::Round(($y + $h) / $g, [System.MidpointRounding]::AwayFromZero))
                [void]$all.Add($k)
                # Big, but not nearly the whole display. A rect over 60% of
                # the panel is a root, a content area or a bar, and every
                # full-screen app has the same handful - so a set built from
                # size alone held ten rects that were identical across apps,
                # and a two-hour session merged the launcher, filemanager,
                # notepad, settings, tips and calculator into one scene.
                # Identity lives between the containers and the noise.
                if ($dispArea -gt 1) {
                    $share = 1000.0 * $w * $h / $dispArea
                    if ($share -ge $floor -and $share -le 600.0) { [void]$big.Add($k) }
                }
            }
        }
        if ($l.Contains('SURFACE_NODE[')) {
            $n = $rxNm.Match($l)
            if ($n.Success) { $tok = "W:" + $n.Groups[1].Value; [void]$all.Add($tok); [void]$big.Add($tok) }
        }
    }

    $src = if ($big.Count -gt 0) { $big } else { $all }
    $sb  = New-Object System.Text.StringBuilder
    foreach ($k in ($src | Sort-Object)) { [void]$sb.Append($k); [void]$sb.Append(';') }
    [void]$sb.Append("N:"); [void]$sb.Append([int][math]::Floor($all.Count / 4.0))
    $algo = [System.Security.Cryptography.MD5]::Create()
    $raw  = $algo.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($sb.ToString()))
    $algo.Dispose()
    $hex = (([System.BitConverter]::ToString($raw)) -replace '-','').Substring(0, 16)
    return [PSCustomObject]@{ Hash = ("{0}/{1}-{2}" -f $src.Count, $all.Count, $hex); Big = $big; All = $all }
}

# --- the loop --------------------------------------------------------------

# Capture at the rate the device will answer, and work out what it meant
# later.
#
# The full loop spends about 3 seconds a sample, and only 0.6 of that is the
# device: the rest is parsing the tree, building the scene, reading the window
# table and resolving occlusion - every one of which can be redone from the
# saved dumps, offline, as many times as we like. Doing it live buys nothing
# and costs most of the sampling rate.
#
# So this loop does the irreducible minimum: pull both dumps, decide from the
# raw text whether this is a scene already seen, and if it is not, write the
# dumps and take a picture. Features come from -RsRebuildAll afterwards.
#
# Repeats cost two dumps and a regex pass. Only a new scene pays for storage
# and a screenshot, which on a phone held on one screen is almost never.
function Invoke-RsFastCapture {
    param([int] $Seconds, [string] $Dir, [int] $Tol = 650, [int] $ShotEvery = 5)

    if (-not (Find-ShotCommand)) { return }
    $stamp   = (Get-Date).ToString("yyyyMMdd_HHmmss")
    $session = Join-Path $Dir $stamp
    [void](New-Item -ItemType Directory -Force -Path $session)
    $csv = Join-Path $session "samples.csv"

    # Pictures stay on the device until the end.
    #
    # Per scene the old path paid 130ms to delete the last frame and 75ms to
    # pull the new one back, for a file nothing reads until the session is
    # over. Writing each to its own name on the device removes the delete,
    # and one directory transfer at the end costs 227ms for thirteen files
    # where thirteen separate pulls cost 975.
    #
    # Freeing those 205ms also lets the snapshot move ahead of the client
    # dump and the file writes, which is what matters more: the picture used
    # to be taken 810ms after the tree read that defined the scene, and a
    # third of scenes lasted under two seconds.
    $devDir = "/data/local/tmp/arkshots"
    Invoke-HdcRaw "shell `"rm -rf $devDir; mkdir -p $devDir`"" | Out-Null

    # Every sample's render tree, kept.
    #
    # In a raw subfolder, not beside the scenes: the rebuild walks
    # *.rstree.txt in the session root at about a second each, so putting
    # 13,000 sample dumps there would turn a three-minute rebuild into a
    # four-hour one. The scenes stay where the rebuild expects them; this is
    # the full stream, for whatever a later extractor wants to ask of it.
    #
    # The dump is already in hand - it is fetched every sample to compute the
    # signature and was simply discarded on repeats - so this costs a disk
    # write and nothing on the device.
    $rawDir = Join-Path $session "raw"
    [void](New-Item -ItemType Directory -Force -Path $rawDir)

    # The display, once. It does not change during a session, and without it
    # an offline rebuild has to guess the panel, the density and the refresh
    # rate - or ask a device that may not be attached any more.
    Invoke-HdcRaw "shell `"hidumper -s DisplayManagerService -a '-a'`"" |
        Set-Content (Join-Path $session "display.txt")

    Write-Host ""
    Write-Host ("capturing to {0} for {1}s - Ctrl+C to stop early" -f $session, $Seconds) -ForegroundColor Cyan
    Write-Host  "one render-tree read per sample. Nothing is computed from it here:" -ForegroundColor DarkGray
    Write-Host  "the dumps are kept and the features derived afterwards, offline." -ForegroundColor DarkGray
    Write-Host ""
    Write-Host ("{0,6}  {1,-7} {2,-9} {3}" -f "n", "scenes", "ms", "note")

    $t0 = Get-Date
    $index = @(); $idx = 0; $n = 0; $failed = 0
    $sw = New-Object System.Diagnostics.Stopwatch
    # A scene does not get its picture when it first appears. It gets one on
    # its first REPEAT, when the screen has demonstrably held still across a
    # sample - taken at first sight, the shot landed while the display was
    # still moving into the scene, and a third of scenes last under two
    # seconds.
    $pending  = @{}          # scene key -> stem still waiting for a picture
    $prevKey  = ""
    $lastShot = (Get-Date).AddSeconds(-$ShotEvery)
    $shots = 0; $periodic = 0

    while (((Get-Date) - $t0).TotalSeconds -lt $Seconds) {
        $sw.Restart()
        # The render tree first, and nothing else until we know whether this
        # sample is worth anything. The client dump costs 193ms and is only
        # ever WRITTEN for a scene we have not seen - it used to be fetched
        # every sample and discarded on every repeat, which on a phone held
        # on one screen is almost all of them.
        $rs = @(Invoke-RsDump -Arg 'RSTree')
        if ($rs.Count -lt 5) { $failed++; continue }
        $sig = Get-RsQuickSig -Lines $rs
        if ($sig.Hash -eq "") { $failed++; continue }
        $n++
        $raw = "s{0:D6}" -f $n
        $rs | Set-Content (Join-Path $rawDir "$raw.rstree.txt")

        $hit = $null
        foreach ($p in $index) {
            if ((Get-SetHold $sig.All $p.All) -ge $Tol) { $hit = $p; break }
        }
        $shot = ""; $stem = ""
        if ($hit) {
            $hit.Dwell++
            $key = $hit.Sig
            # The same scene twice running: the screen held across a sample,
            # so a picture taken now shows what the dump described.
            if ($key -eq $prevKey -and $pending.ContainsKey($key)) {
                $st = $pending[$key]
                Invoke-HdcRaw ("shell `"{0} {1}/{2}{3}`"" -f $script:ShotBin, $devDir, $st, $script:ShotExt) | Out-Null
                $shot = $st + $script:ShotExt
                $pending.Remove($key)
                $shots++
                $lastShot = Get-Date
            }
        } else {
            $idx++
            $stem = "{0:D4}" -f $idx
            $key  = $sig.Hash
            $index += [PSCustomObject]@{ Sig = $key; All = $sig.All; Dwell = 1 }
            # Tags now, picture later. The two dumps join by node id and that
            # join is exact only because little moves between them.
            $cl = @(Invoke-RsDump -Arg 'client')
            $rs | Set-Content (Join-Path $session "$stem.rstree.txt")
            $cl | Set-Content (Join-Path $session "$stem.client.txt")
            $pending[$key] = $stem
        }

        # The backstop. A scene seen once and never again never earns a
        # stable pair, so without this it would have no picture at all - and
        # a periodic frame taken while the display sat on one screen is the
        # best evidence available for it. Which scene each belongs to is
        # settled offline, against the signature of the sample it was taken
        # at, which is why the sample number is in the name.
        if (-not $shot -and ((Get-Date) - $lastShot).TotalSeconds -ge $ShotEvery) {
            $pn = "p{0:D6}" -f $n
            Invoke-HdcRaw ("shell `"{0} {1}/{2}{3}`"" -f $script:ShotBin, $devDir, $pn, $script:ShotExt) | Out-Null
            $shot = $pn + $script:ShotExt
            $lastShot = Get-Date
            $periodic++
        }

        $row = [PSCustomObject]@{
            ts    = (Get-Date).ToString("HH:mm:ss.fff")
            sig   = $key
            stem  = $stem
            shot  = $shot
            raw   = $raw
            nsig  = $sig.All.Count
            ms    = [int]$sw.ElapsedMilliseconds
        }
        if (Test-Path $csv) { $row | Export-Csv -Path $csv -NoTypeInformation -Append }
        else                { $row | Export-Csv -Path $csv -NoTypeInformation }

        $prevKey = $key
        if (-not $hit) {
            Write-Host ("{0,6}  {1,-7} {2,-9} {3}" -f $n, $idx, $row.ms, "new scene -> $stem") -ForegroundColor Green
        } elseif ($n % 25 -eq 0) {
            Write-Host ("{0,6}  {1,-7} {2,-9} {3}" -f $n, $idx, $row.ms, "same scene") -ForegroundColor DarkGray
        }
    }

    # One transfer for every picture taken, now that the device is free.
    # Against the number of pictures TAKEN, not the number of scenes. Scene
    # shots and periodic ones both land here, so comparing to the scene count
    # printed "51 of 39 arrived" - and worse, it would have called a transfer
    # complete and wiped the device while pictures were still missing, as
    # long as more than one per scene had made it back.
    $want = $shots + $periodic
    if ($want -gt 0) {
        Write-Host ""
        Write-Host ("  pulling {0} pictures back in one transfer..." -f $want) -ForegroundColor DarkGray
        $t0r = Get-Date
        Invoke-HdcRaw ("file recv {0} `"{1}`"" -f $devDir, $session) | Out-Null
        # hdc drops a directory transfer into a subfolder named after it.
        $sub = Join-Path $session "arkshots"
        if (Test-Path $sub) {
            Get-ChildItem -Path $sub -File | ForEach-Object {
                Move-Item -Path $_.FullName -Destination (Join-Path $session $_.Name) -Force
            }
            Remove-Item -Path $sub -Recurse -Force -ErrorAction SilentlyContinue
        }
        $got = @(Get-ChildItem -Path $session -Filter ("*" + $script:ShotExt)).Count
        Write-Host ("  {0} of {1} arrived in {2:N1}s" -f $got, $want, ((Get-Date) - $t0r).TotalSeconds) -ForegroundColor DarkGray
        if ($got -lt $want) {
            Write-Host ("  {0} missing. They are still on the device at {1}" -f ($want - $got), $devDir) -ForegroundColor Yellow
        } else {
            Invoke-HdcRaw "shell `"rm -rf $devDir`"" | Out-Null
        }
    }

    # dwell per scene, written where the rebuild can find it
    $dw = Join-Path $session "dwell.csv"
    $index | Select-Object Sig, Dwell | Export-Csv -Path $dw -NoTypeInformation
    $el = ((Get-Date) - $t0).TotalSeconds
    $rawMB = 0
    if (Test-Path $rawDir) {
        $rawMB = [math]::Round((Get-ChildItem $rawDir -File | Measure-Object Length -Sum).Sum / 1MB, 0)
    }
    Write-Host ""
    Write-Host ("captured {0:N0}s: {1} samples, {2} distinct scenes, {3} failed" -f $el, $n, $idx, $failed) -ForegroundColor Green
    Write-Host ("  raw stream: {0} dumps, {1:N0} MB in {2}" -f $n, $rawMB, $rawDir) -ForegroundColor DarkGray
    Write-Host ("  pictures: {0} on a stable pair, {1} periodic, {2} scenes never settled" -f `
        $shots, $periodic, $pending.Count) -ForegroundColor DarkGray
    if ($n -gt 0) { Write-Host ("  {0:N2} s per sample" -f ($el / $n)) -ForegroundColor Green }
    Write-Host ("  -> {0}" -f $session)
    Write-Host ""
    Write-Host "  derive the two vectors when you are done:" -ForegroundColor DarkCyan
    Write-Host ("  python tools/cluster/rs_pattern.py {0} --out patterns.csv" -f $Dir) -ForegroundColor DarkCyan
}

Invoke-RsFastCapture -Seconds $Seconds -Dir $Dir -Tol $Tol -ShotEvery $ShotEvery
