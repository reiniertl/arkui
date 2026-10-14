@echo off
setlocal
rem Creates the virtual environment on first run, then just runs. Idempotent,
rem so every later invocation is plain:  cluster.cmd <capture-dir>
set HERE=%~dp0
set VENV=%HERE%.venv
if not exist "%VENV%\Scripts\python.exe" (
  echo creating virtual environment in %VENV%
  py -3 -m venv "%VENV%" || python -m venv "%VENV%" || goto :novenv
  "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
  if defined ARKUI_WHEELS (
    rem Airgapped: populate that folder on an online box with
    rem   pip download -r requirements.txt -d D:\wheels
    echo installing from %ARKUI_WHEELS% with no index
    "%VENV%\Scripts\python.exe" -m pip install --no-index --find-links "%ARKUI_WHEELS%" -r "%HERE%requirements.txt" || goto :nodeps
  ) else (
    "%VENV%\Scripts\python.exe" -m pip install -r "%HERE%requirements.txt" || goto :nodeps
  )
)
"%VENV%\Scripts\python.exe" "%HERE%cluster.py" %*
exit /b %ERRORLEVEL%

:novenv
echo.
echo Could not create a virtual environment. Is Python 3.10+ on PATH?
echo   py -3 --version
exit /b 1

:nodeps
echo.
echo Dependency install failed. If this machine has no package index, run this
echo on a machine that does:
echo     pip download -r requirements.txt -d D:\wheels
echo copy the folder across, then:
echo     set ARKUI_WHEELS=D:\wheels
echo     cluster.cmd ...
echo and delete the half-built .venv first.
exit /b 1
