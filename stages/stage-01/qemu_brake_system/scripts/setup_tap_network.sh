#!/usr/bin/env bash
set -euo pipefail
bridge=brakebr0
ports=(brakectrl0 brakeecu1 brakeecu2 brakeecu3 brakeecu4)
ip link show "$bridge" >/dev/null 2>&1 || ip link add name "$bridge" type bridge
for tap in "${ports[@]}"; do
  ip link show "$tap" >/dev/null 2>&1 || ip tuntap add dev "$tap" mode tap user brain
  ip link set "$tap" master "$bridge"
  ip link set "$tap" up
done
ip link set "$bridge" type bridge mcast_snooping 0
ip link set "$bridge" up
echo "Ready: $bridge (${ports[*]}), multicast snooping disabled"
