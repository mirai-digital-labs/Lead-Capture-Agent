@echo off
if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Please run setup.bat first!
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat
python main.py %*
pause
