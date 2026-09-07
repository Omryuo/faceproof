#!/usr/bin/env bash
# One-command runner for FaceProof: auto-setups venv, models, examples, and runs the project.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR=".venv"
PY="$VENV_DIR/bin/python"

banner() { printf '\n\033[1;36m=== %s ===\033[0m\n' "$1"; }
info() { printf '\033[1;32m[+] %s\033[0m\n' "$1"; }

# 1. Environment setup check
if [[ ! -d "$VENV_DIR" || ! -f "$PY" ]]; then
  info "Virtual environment not found. Setting up .venv and installing dependencies..."
  python3 -m venv "$VENV_DIR"
  "$PY" -m pip install --upgrade pip --quiet
  "$PY" -m pip install -r requirements-dev.txt --quiet
fi

# 2. Models check
YUNET="models/face_detection_yunet_2023mar.onnx"
SFACE="models/face_recognition_sface_2021dec.onnx"
if [[ ! -f "$YUNET" || ! -f "$SFACE" ]]; then
  info "Downloading face detection & recognition ONNX models..."
  "$PY" -m faceproof.cli fetch-models
fi

# 3. Examples check
PROBE="examples/probe.jpg"
if [[ ! -f "$PROBE" ]]; then
  info "Downloading example probe image..."
  "$PY" scripts/fetch_example.py
fi

# 4. Execution logic
if [[ $# -eq 0 ]]; then
  info "Starting full end-to-end demo..."
  ./scripts/demo.sh
else
  "$PY" -m faceproof.cli "$@"
fi
