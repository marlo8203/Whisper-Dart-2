#!/usr/bin/env bash
# Launch the Whisper Dart Gradio UI.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."
exec python -m ui.whisper_app "$@"
