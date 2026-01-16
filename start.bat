@echo off
REM Auto-commit and push to GitHub before running the bot
REM Usage: start.bat

cd /d "%~dp0"

echo === Syncing with GitHub ===

REM Check if git repo exists
if not exist ".git" (
    echo Initializing git repository...
    git init
    git remote add origin https://github.com/Some-creator/TradingBot.git
)

REM Add all changes
git add -A

REM Commit with timestamp
for /f "tokens=1-4 delims=/ " %%a in ('date /t') do set DATE=%%c-%%a-%%b
for /f "tokens=1-2 delims=: " %%a in ('time /t') do set TIME=%%a:%%b

git commit -m "Auto-update: %DATE% %TIME%"

REM Push to GitHub
git push origin main

echo === Starting Trading Bot ===
python run.py

pause
