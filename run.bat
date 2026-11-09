@echo off
cd /d "%~dp0"
python -c "import PySide6, requests, winrt.windows.media.control" 2>nul || (
    echo Installing requirements...
    python -m pip install -r requirements.txt
)
start "" pythonw main.py
