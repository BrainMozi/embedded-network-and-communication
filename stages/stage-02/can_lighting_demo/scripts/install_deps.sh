#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y python3.14-venv can-utils
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c 'import can; print("python-can", can.__version__)'
candump --help >/dev/null
echo "Dependencies ready."
