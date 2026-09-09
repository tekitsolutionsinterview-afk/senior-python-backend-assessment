#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" --version
"$PYTHON_BIN" -m pip install -r requirements.txt
"$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
