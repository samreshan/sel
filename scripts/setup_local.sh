#!/usr/bin/env bash
# Set up Sel's local models on Linux x86_64 (CPU only, no root needed).
#
#   scripts/setup_local.sh            # speech + Ollama + qwen2.5:7b
#   SEL_LLM_MODEL=qwen2.5:3b scripts/setup_local.sh   # faster, much weaker; non-commercial licence (MODELS.md)
#
# Installs into the repository: .venv/ (Python packages), .tools/ollama/
# (the Ollama binary), models/ (Whisper, Piper voices, Ollama models).
# Nothing is installed system-wide. macOS/Windows: install Ollama from
# https://ollama.com instead and skip that step.
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${SEL_LLM_MODEL:-qwen2.5:7b}"
WHISPER="${SEL_WHISPER_MODEL:-small}"

echo "==> Python environment (.venv)"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[speech,tts]"

echo "==> Whisper model: $WHISPER"
.venv/bin/python - "$WHISPER" <<'EOF'
import sys
from faster_whisper import WhisperModel
WhisperModel(sys.argv[1], device="cpu", compute_type="int8", download_root="models/whisper")
print("ok")
EOF

echo "==> Piper voices for synthetic test calls"
mkdir -p models/piper
base=https://huggingface.co/rhasspy/piper-voices/resolve/main
for v in en/en_US/lessac/medium/en_US-lessac-medium en/en_US/ryan/medium/en_US-ryan-medium; do
  name=$(basename "$v")
  for ext in onnx onnx.json; do
    [ -f "models/piper/$name.$ext" ] || curl -fsSL -o "models/piper/$name.$ext" "$base/$v.$ext"
  done
done

if ! command -v ollama >/dev/null && [ ! -x .tools/ollama/bin/ollama ]; then
  echo "==> Ollama (local LLM server) into .tools/ollama"
  mkdir -p .tools/ollama
  url=https://github.com/ollama/ollama/releases/latest/download
  if curl -fsSL "$url/ollama-linux-amd64.tar.zst" -o /tmp/ollama.tar.zst 2>/dev/null; then
    tar --zstd -xf /tmp/ollama.tar.zst -C .tools/ollama
  else
    curl -fsSL "$url/ollama-linux-amd64.tgz" | tar -xz -C .tools/ollama
  fi
fi
OLLAMA=$(command -v ollama || echo .tools/ollama/bin/ollama)

if ! curl -s http://127.0.0.1:11434/api/version >/dev/null; then
  echo "==> Starting Ollama (log: .tools/ollama.log)"
  OLLAMA_MODELS="$PWD/models/ollama" nohup "$OLLAMA" serve > .tools/ollama.log 2>&1 &
  for _ in $(seq 30); do curl -s http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
fi

echo "==> Pulling $MODEL"
OLLAMA_MODELS="$PWD/models/ollama" "$OLLAMA" pull "$MODEL"

cat <<EOF

Done. Start Sel with:
  SEL_LLM_MODEL=$MODEL .venv/bin/python -m sel serve
and open http://127.0.0.1:8765

If Ollama is not running later, start it with:
  OLLAMA_MODELS=$PWD/models/ollama $OLLAMA serve
EOF
