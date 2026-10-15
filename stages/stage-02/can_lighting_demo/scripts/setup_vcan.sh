#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-vcan0}"
if ! command -v ip >/dev/null 2>&1; then
  echo "缺少 iproute2；请先安装。" >&2
  exit 1
fi
sudo modprobe vcan
if ! ip link show "$IFACE" >/dev/null 2>&1; then
  sudo ip link add dev "$IFACE" type vcan
fi
sudo ip link set dev "$IFACE" up
ip -details link show "$IFACE"
echo "Ready: $IFACE (SocketCAN virtual interface)"
