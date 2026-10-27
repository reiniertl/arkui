# ---------------------------------------------------------------------------
#  py.ps1 - run a tool of this repository against its pinned dependencies.
#
#      .\tools\py.ps1 cluster\rs_pattern.py C:\ark_ui\captures --out p.csv
#
#  Builds .venv from tools/cluster/requirements.txt the first time and uses
#  it thereafter. The capture machine has the py launcher and no numpy, and
#  an instruction in a README to "pip install" is not a dependency anyone
#  can rebuild a result from: the pins are the measurement's, so the thing
#  that installs them belongs in the repository beside them.
#
#  Do not run this while a capture is in flight. Resolving and unpacking
#  wheels is real CPU on the machine whose CPU the experiment is reading.
# ---------------------------------------------------------------------------
param(
    [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)]
    [string[]] $Args
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$venv = Join-Path $root ".venv"
$exe  = Join-Path $venv "Scripts\python.exe"
$req  = Join-Path $here "cluster\requirements.txt"

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
    $script = Join-Path $here $script
}
& $exe $script @($Args[1..($Args.Length - 1)])
exit $LASTEXITCODE
