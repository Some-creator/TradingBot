@echo off
REM Quick push to GitHub
REM Usage: push.bat "Your commit message"

cd /d "%~dp0"

set MSG=%~1
if "%MSG%"=="" set MSG=Update

git add -A
git commit -m "%MSG%"
git push origin master

echo Done!
pause
