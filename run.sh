#!/usr/bin/env bash
# Quick launcher for PRMAnalyzer
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "[+] Creating virtualenv (.venv)"
    python3 -m venv .venv
fi

source .venv/bin/activate

echo "[+] Installing dependencies"
pip install --quiet -r requirements.txt

echo "[+] Starting PRMAnalyzer on http://127.0.0.1:8080"
python app.py
