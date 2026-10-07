@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0.build_tools;%PYTHONPATH%"
python "%~dp0pmdg_livery_installer.py"

