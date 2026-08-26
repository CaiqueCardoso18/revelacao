#!/bin/bash
# Para o revelação de rodar em segundo plano automaticamente. Suas fotos e
# pastas não são apagadas -- só o serviço de fundo é desligado.
set -e
PLIST="$HOME/Library/LaunchAgents/com.revelacao.agent.plist"

if [ -f "$PLIST" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm "$PLIST"
  echo "Pronto -- o revelação não vai mais subir sozinho ao ligar o Mac."
else
  echo "Não havia nada instalado."
fi
echo "Pode fechar esta janela."
