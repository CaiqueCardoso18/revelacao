#!/bin/bash
# Duplo clique neste arquivo abre o revelação. Não precisa saber o que é terminal --
# esta janela só mostra o progresso; ela pode ficar aberta atrás do navegador.
set -e
cd "$(dirname "$0")"

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

echo ""
echo "Abrindo o revelação — nenhuma foto sai desta máquina."
echo "Para fechar, feche esta janela."
echo ""

open "http://127.0.0.1:8420" &

.venv/bin/python -m backend.main
