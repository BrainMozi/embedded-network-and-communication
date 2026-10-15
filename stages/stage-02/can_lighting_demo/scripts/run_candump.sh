#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec candump -ta vcan0
