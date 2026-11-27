#!/bin/zsh
# Serve Ternary Bonsai 2.5 27B (Q2_0) with llama.cpp for Aura's "llama.cpp (local)" provider.
#
# Aura now starts and stops llama-server itself (src/aura/llama_server.py), so you don't need
# this. It's only for trying Bonsai by hand, for example with BONSAI_PORT=8090 while Aura runs
# (Aura uses 8080 and reports the port as busy if something else holds it).
#
#   --alias      a short model name (without it the name is the file path, which Aura rejects)
#   --jinja      the model's chat template, which Aura's tool calls need
#   -c 16384     matches Aura's context_tokens, so memory compaction triggers at the right size
#   --parallel 1 one conversation at a time, so the whole context window serves that one turn
#   --host       localhost only; Aura refuses a llama.cpp URL on any other host
#
# Extra arguments pass through to llama-server. A server started by hand isn't Aura's: Aura
# sends its own key to the llama-server it starts, and reports this port as busy.
set -euo pipefail

MODEL="${BONSAI_GGUF:-$HOME/models/bonsai-2.5/Ternary-Bonsai-2.5-27B-Q2_0.gguf}"
PORT="${BONSAI_PORT:-8080}"

[[ -f "$MODEL" ]] || { echo "Model not found: $MODEL" >&2; exit 1; }

exec llama-server -m "$MODEL" --alias bonsai-2.5-27b \
  --host 127.0.0.1 --port "$PORT" \
  --jinja -c 16384 --parallel 1 -ngl 99 "$@"
