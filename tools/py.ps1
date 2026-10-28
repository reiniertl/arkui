# ---------------------------------------------------------------------------
#  py.ps1 - run a tool of this repository against its pinned dependencies.
#
#      \\vm\ark_ui\tools\py.ps1 cluster\rs_pattern.py captures --out p.csv
#
#  Builds a venv from tools/cluster/requirements.txt the first time and uses
#  it thereafter. The capture machine has the py launcher and no numpy, and
#  an instruction in a README to "pip install" is not a dependency anyone
#  can rebuild a result from: the pins are the measurement's, so the thing
#  that installs them belongs in the repository beside them.
#
#  The repository lives on the VM and reaches the capture machine as a
#  read-only share, so nothing is built or run beside these scripts. The
#  venv and a copy of tools/ both live in the working folder, $env:ARK_WORK
#  or else ~\ark_work. The copy is refreshed from the share on every run and
#  the tool runs from it: run from the share itself, every import Python
#  attempts is a round trip to the VM, and over the relayed link that made a
#  0.7 s check take 157 s. Nothing in the copy is edited; it is overwritten.
#
#  Do not run this while a capture is in flight. Resolving and unpacking
#  wheels is real CPU on the machine whose CPU the experiment is reading.
# ---------------------------------------------------------------------------
param(
    [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)]
    [string[]] $Args
)

$ErrorActionPreference = "Stop"
$here  = Split-Path -Parent $MyInvocation.MyCommand.Path
$work  = if ($env:ARK_WORK) { $env:ARK_WORK } else { Join-Path $HOME "ark_work" }
$tools = Join-Path $work "tools"
$venv  = Join-Path $work ".venv"
$exe   = Join-Path $venv "Scripts\python.exe"

New-Item -ItemType Directory -Force $work | Out-Null
# /XD keeps each side's __pycache__ out of the mirror, so the copy's own
# compiled files survive between runs.
robocopy $here $tools /MIR /MT:16 /XD __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "could not refresh $tools from $here" }
$req = Join-Path $tools "cluster\requirements.txt"

if (-not (Test-Path $exe)) {
    Write-Host "building .venv (first run only)"
    & py -m venv $venv
    if (-not (Test-Path $exe)) { throw "venv build produced no interpreter" }
    & $exe -m pip install --quiet --upgrade pip
    # --only-binary so a missing wheel says so instead of starting a compile
    # that will fail later and more obscurely.
    & $exe -m pip install --quiet --only-binary=:all: -r $req
    if ($LASTEXITCODE -ne 0) { throw "pinned dependencies did not install" }
}

$script = $Args[0]
if (-not [System.IO.Path]::IsPathRooted($script)) {
    $script = Join-Path $tools $script
}
& $exe $script @($Args[1..($Args.Length - 1)])
exit $LASTEXITCODE
