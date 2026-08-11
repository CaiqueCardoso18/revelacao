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
    echo Instale manualmente em https://astral.sh/uv e rode este arquivo de novo.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Instalando o Python 3.11 isolado ^(so acontece uma vez^)...
    uv venv --python 3.11 .venv
    if errorlevel 1 (
        echo Falha ao criar o ambiente. Veja a mensagem acima.
        pause
        exit /b 1
    )
)

echo Verificando dependencias...
uv pip install -q -p .venv -r requirements.txt
if errorlevel 1 (
    echo Falha ao instalar dependencias. Veja a mensagem acima.
    pause
    exit /b 1
)

echo.
echo Abrindo o revelacao — nenhuma foto sai desta maquina.
echo Para fechar, feche esta janela.
echo.

start "" "http://127.0.0.1:8420"
".venv\Scripts\python.exe" -m backend.main

endlocal
