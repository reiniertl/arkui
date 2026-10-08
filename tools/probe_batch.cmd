@echo off
REM Wrapper so the probe runs without changing the execution policy.
REM Usage:  probe_batch.cmd -Setup
REM         probe_batch.cmd -Plan plan.txt
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0probe_batch.ps1" %*
