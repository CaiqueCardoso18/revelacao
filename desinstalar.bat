@echo off
setlocal
cd /d "%~dp0"

set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
if exist "%STARTUP%\revelacao.vbs" del "%STARTUP%\revelacao.vbs"
if exist "%~dp0run_hidden.vbs" del "%~dp0run_hidden.vbs"

echo Pronto -- o revelacao nao vai mais subir sozinho ao ligar o Windows.
echo (Se ele ja estiver rodando agora, continua ate voce reiniciar o Windows
echo  ou fechar o processo manualmente.)
pause
