#!/usr/bin/env bash
set -euo pipefail
for tap in brakectrl0 brakeecu1 brakeecu2 brakeecu3 brakeecu4; do
  ip link show "$tap" >/dev/null 2>&1 && ip link delete "$tap" || true
done
ip link show brakebr0 >/dev/null 2>&1 && ip link delete brakebr0 type bridge || true
