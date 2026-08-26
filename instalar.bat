@echo off
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
where uv >nul 2>nul
if errorlevel 1 (
    echo Instalando o gerenciador de pacotes ^(uv^)...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
where uv >nul 2>nul
if errorlevel 1 (
    echo Nao foi possivel instalar o uv automaticamente.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Instalando o Python 3.11 isolado ^(so acontece uma vez^)...
    uv venv --python 3.11 .venv
)

echo Verificando dependencias...
uv pip install -q -p .venv -r requirements.txt

if not exist "data" mkdir data

echo Registrando o revelacao para iniciar sozinho com o Windows...

set "VBS=%~dp0run_hidden.vbs"
> "%VBS%" echo Set WshShell = CreateObject("WScript.Shell")
>> "%VBS%" echo pyExe = "%~dp0.venv\Scripts\pythonw.exe"
>> "%VBS%" echo WshShell.CurrentDirectory = "%~dp0"
>> "%VBS%" echo WshShell.Run Chr(34) ^& pyExe ^& Chr(34) ^& " -m backend.main", 0, False

set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
copy /y "%VBS%" "%STARTUP%\revelacao.vbs" >nul
if errorlevel 1 (
    echo Falha ao registrar o inicio automatico.
    pause
    exit /b 1
)

wscript.exe "%VBS%"

echo.
echo Pronto -- o revelacao ja esta rodando em segundo plano, e vai subir
echo sozinho sempre que voce ligar o Windows daqui pra frente.
echo.
echo Se essa e a primeira vez, uma aba do navegador deve abrir sozinha em
echo alguns segundos pedindo pra conectar sua conta -- e so confirmar.
echo.
pause
