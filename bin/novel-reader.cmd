@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PLUGIN_ROOT=%SCRIPT_DIR%.."
set "PYTHONPATH=%PLUGIN_ROOT%\src;%PYTHONPATH%"
python -m novel_reader.cli %*

