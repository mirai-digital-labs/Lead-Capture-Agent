@echo off
echo === Lead Capture Agent Setup ===
python --version >nul 2>&1
if errorlevel 1 (
    echo Python is not installed! Installing Python 3.12 via winget...
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    echo Please restart your terminal after Python installation finishes and run setup.bat again.
    pause
    exit /b 1
)

echo Creating virtual environment...
python -m venv .venv
call .venv\Scripts\activate.bat

echo Installing Python packages...
pip install -r requirements.txt

echo Installing Playwright Chromium browser...
playwright install chromium

echo.
echo Setup complete! Run "run.bat" or "python main.py" to start capturing leads.
pause
