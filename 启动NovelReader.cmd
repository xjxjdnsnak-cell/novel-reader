@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "MODEL_PATH=%USERPROFILE%\.cache\modelscope\hub\models\Qwen\Qwen3-Embedding-0.6B"

title Novel Reader Launcher

echo.
echo ===============================================
echo              Novel Reader Launcher
echo ===============================================
echo.
echo Project:
echo %ROOT%
echo.
echo Qwen model:
echo %MODEL_PATH%
echo.
echo Choose a mode. Press Enter for option 3.
echo.
echo   1. Claude plugin mode + Qwen Embedding
echo   2. Claude plugin dangerous mode + Qwen Embedding
echo   3. Web console + Claude Bridge + Qwen Embedding
echo   4. Web console dangerous mode + Claude Bridge + Qwen Embedding
echo   5. Qwen Embedding only
echo   6. Web console only, Embedding disabled
echo.
set /p CHOICE=Enter number and press Enter: 
if "%CHOICE%"=="" set "CHOICE=3"

if "%CHOICE%"=="1" goto claude_normal
if "%CHOICE%"=="2" goto claude_danger
if "%CHOICE%"=="3" goto web_normal
if "%CHOICE%"=="4" goto web_danger
if "%CHOICE%"=="5" goto qwen_only
if "%CHOICE%"=="6" goto web_no_embedding

echo.
echo Invalid choice.
pause
exit /b 1

:claude_normal
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%bin\start-claude-plugin.ps1" -ModelPath "%MODEL_PATH%" -ClaudePermissionMode normal
goto end

:claude_danger
echo.
echo Dangerous mode requires typing DANGEROUS in the next prompt.
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%bin\start-claude-plugin.ps1" -ModelPath "%MODEL_PATH%" -ClaudePermissionMode dangerous
goto end

:web_normal
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%bin\start-web.ps1" -ModelPath "%MODEL_PATH%" -EnableClaudeChat -ClaudePermissionMode normal -Background -OpenBrowser
goto end

:web_danger
echo.
echo Dangerous mode requires typing DANGEROUS in the next prompt.
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%bin\start-web.ps1" -ModelPath "%MODEL_PATH%" -EnableClaudeChat -ClaudePermissionMode dangerous -Background -OpenBrowser
goto end

:qwen_only
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%bin\start-novel-reader.ps1" -Client none -ModelPath "%MODEL_PATH%"
goto end

:web_no_embedding
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%bin\start-web.ps1" -NoEmbedding -EnableClaudeChat -ClaudePermissionMode normal -Background -OpenBrowser
goto end

:end
echo.
echo Qwen Embedding status:
powershell -NoProfile -ExecutionPolicy Bypass -Command "$found=$false; foreach($p in 8081..8085){ try { $h=Invoke-RestMethod -Uri ('http://127.0.0.1:'+$p+'/health') -TimeoutSec 1; if ($h.ok -and $h.model_loaded) { Write-Host ('  OK - ' + $h.model + ' on ' + $h.device + ' at 127.0.0.1:' + $p); $found=$true } } catch {} }; if(-not $found){ Write-Host '  Not running or not reachable' }"
echo.
echo Done.
pause
