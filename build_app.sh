#!/bin/bash
# Build a double-clickable "Handy Transcribe.app" with PyInstaller.
# Run from the project root. The venv must already have the deps installed.
set -e
VENV="$HOME/.venvs/handy-transcribe"
PY="$VENV/bin/python"

"$VENV/bin/pip" install --quiet pyinstaller

cd "$(dirname "$0")"
"$PY" -m PyInstaller \
  --noconfirm --clean --windowed \
  --name "Handy Transcribe" \
  --add-data "web:web" \
  --collect-data onnx_asr \
  --collect-submodules onnx_asr \
  --copy-metadata onnx-asr \
  --copy-metadata onnxruntime \
  app.py

echo
echo "Built: dist/Handy Transcribe.app"
echo "Move it to /Applications and pin to the Dock if you like."
echo "Note: the Parakeet model is still read live from Handy's folder, so Handy"
echo "must remain installed with the parakeet-tdt-0.6b-v3 model downloaded."