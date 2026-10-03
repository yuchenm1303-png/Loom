@echo off
setlocal
set "PY=%~dp0..\.venv\Scripts\python.exe"
set "REC=%~dp0recorder.py"
set "OUT=%~dp0results.jsonl"
"%PY%" "%REC%" --out "%OUT%" --record "%*"
endlocal