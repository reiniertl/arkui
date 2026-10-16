@echo off
setlocal
rem Builds the virtual environment on first run, then just runs. Idempotent.
rem
rem The marker file, not python.exe, is what says "ready". A venv whose
rem dependency install failed still has an interpreter in it, and testing for
rem that turned one legible pip error into an ImportError on the next run.
set HERE=%~dp0
set VENV=%HERE%.venv
set STAMP=%VENV%\.deps-ok
set VPY=%VENV%\Scripts\python.exe

if exist "%STAMP%" goto :run

rem Pick an interpreter the pinned wheels exist for. On a Python too new for
rem them, pip silently falls back to the numpy SOURCE tarball and asks for a C
rem compiler - which is not a thing this machine is expected to have, and the
rem error it prints is about meson rather than about the Python version.
set PY=
for %%V in (3.12 3.11 3.10 3.13) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY ( py -3 -c "import sys" >nul 2>&1 && set "PY=py -3" )
if not defined PY ( python -c "import sys" >nul 2>&1 && set "PY=python" )
if not defined PY goto :nopython

if exist "%VPY%" goto :haveenv
echo creating virtual environment in %VENV%
echo   interpreter: %PY%
%PY% -m venv "%VENV%" || goto :novenv

:haveenv
for /f "delims=" %%P in ('"%VPY%" -c "import sys;print(sys.version.split()[0])"') do echo   python %%P
"%VPY%" -m pip install --quiet --upgrade pip

rem --only-binary=:all: is the point of this line. Without it a missing wheel
rem becomes a twenty-minute compile attempt that fails anyway; with it pip
rem says which package has no wheel, immediately.
if defined ARKUI_WHEELS (
  rem Airgapped: populate that folder on an online box with
  rem   pip download -r requirements.txt -d D:\wheels
  echo installing from %ARKUI_WHEELS% with no index
  "%VPY%" -m pip install --only-binary=:all: --no-index --find-links "%ARKUI_WHEELS%" -r "%HERE%requirements.txt" || goto :nodeps
  goto :ok
)

"%VPY%" -m pip install --only-binary=:all: -r "%HERE%requirements.txt" && goto :ok

rem The pinned set has no wheel for this interpreter. Take the newest that
rem does: the pins are there for reproducibility, not because the code needs
rem those exact versions, and a working install beats a matching one.
echo.
echo the pinned versions have no wheel for this Python - taking the newest that does
"%VPY%" -m pip install --only-binary=:all: "numpy>=2.1" "scipy>=1.14" "scikit-learn>=1.5" "pillow>=11" || goto :nodeps

:ok
echo ok> "%STAMP%"

:run
"%VENV%\Scripts\python.exe" "%HERE%cluster.py" %*
exit /b %ERRORLEVEL%

:nopython
echo.
echo No Python found. Install 3.12 and re-run:  https://www.python.org/downloads/
echo   py -0p     lists the ones this machine already has
exit /b 1

:novenv
echo.
echo Could not create a virtual environment with %PY%.
echo   py -0p     lists the Python versions this machine has
exit /b 1

:nodeps
echo.
echo Dependency install failed, and no wheel was built from source on purpose.
echo.
echo   py -0p                     which Pythons this machine has
echo   rmdir /s /q "%VENV%"       throw away the half-built environment
echo.
echo If one of them is 3.10, 3.11 or 3.12 this should not have happened - say so.
echo If this machine has no usable package index, run on one that does:
echo     pip download -r requirements.txt -d D:\wheels
echo copy the folder across, then:
echo     set ARKUI_WHEELS=D:\wheels
echo     cluster\cluster.cmd captures\<timestamp>
exit /b 1
