#!/bin/bash
# Duplo clique aqui UMA VEZ. Depois disso o revelação sobe sozinho toda vez
# que você ligar o Mac -- sem essa janela, sem clicar em nada de novo.
# Rodar de novo depois de um "git pull" também é seguro: só reinicia o
# serviço com o código atualizado.
set -e
cd "$(dirname "$0")"
APP_DIR="$(pwd)"

if ! command -v uv >/dev/null 2>&1; then
  export PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "Preparando o revelação pela primeira vez (só acontece uma vez)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if [ ! -d ".venv" ]; then
  echo "Instalando o Python necessário (isolado, não afeta o resto do Mac)..."
  uv venv --python 3.11 .venv
fi

echo "Verificando dependências..."
uv pip install -q -p .venv -r requirements.txt

mkdir -p "$APP_DIR/data"

PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST="$PLIST_DIR/com.revelacao.agent.plist"
mkdir -p "$PLIST_DIR"

cat > "$PLIST" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.revelacao.agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>$APP_DIR/.venv/bin/python</string>
        <string>-m</string>
        <string>backend.main</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$APP_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$APP_DIR/data/agent.log</string>
    <key>StandardErrorPath</key>
    <string>$APP_DIR/data/agent.err.log</string>
</dict>
</plist>
PLISTEOF

# Safe to re-run: stop any previous version of the service before loading
# this one, so re-running after a "git pull" actually picks up new code.
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"

echo ""
echo "Pronto -- o revelação já está rodando em segundo plano, e vai subir"
echo "sozinho sempre que você ligar o Mac daqui pra frente."
echo ""
echo "Se essa é a primeira vez, uma aba do navegador deve abrir sozinha em"
echo "alguns segundos pedindo pra conectar sua conta -- é só confirmar."
echo ""
echo "Pode fechar esta janela agora."
