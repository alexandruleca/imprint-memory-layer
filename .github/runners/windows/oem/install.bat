@echo off
rem dockur/windows runs this once, as the administrator, when Windows finishes
rem installing. The work is in install.ps1; its log goes to the shared folder.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" > "\\host.lan\Data\install.log" 2>&1
