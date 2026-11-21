#!/usr/bin/env bash
# End-to-end streaming lab: three isolated camera networks built with Linux
# network namespaces, real MediaMTX + ffmpeg test-pattern cameras, a simulated
# ONVIF NVR, and the real CameraScope gateway agent.
#
#   lan1  10.10.1.0/24     camera reachable from the cloud by route (VPN / same LAN)  -> "cloud" connection
#   site2 192.168.50.0/24  camera on another network, NOT routable from the cloud      -> gateway A
#   site3 192.168.60.0/24  2-channel ONVIF NVR, NOT routable from the cloud            -> gateway B
#
# The host plays the cloud. iptables drops every NEW connection from the host
# into site2/site3, so the only way in is each gateway's outbound tunnel.
#
# Usage: lab.sh up <mediamtx-binary> <workdir> | lab.sh agent <ns> <workdir> <python> <enrollment-code> <cloud-url> <ca-file> | lab.sh down
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
AGENT_DIR="$(cd "$HERE/../../../gateway/agent" && pwd)"

netns() { # name host_ip ns_ip
  ip netns add "$1"
  ip link add "v-$1" type veth peer name "p-$1"
  ip link set "p-$1" netns "$1"
  ip addr add "$2/24" dev "v-$1"; ip link set "v-$1" up
  ip netns exec "$1" ip addr add "$3/24" dev "p-$1"
  ip netns exec "$1" ip link set "p-$1" up
  ip netns exec "$1" ip link set lo up
  ip netns exec "$1" ip route add default via "$2"
}

camera_conf() { # file user pass paths...
  local f=$1 user=$2 pass=$3; shift 3
  cat > "$f" <<EOF
logLevel: warn
api: no
rtmp: no
hls: no
webrtc: no
srt: no
rtspAddress: :8554
protocols: [tcp, udp]
authMethod: internal
authInternalUsers:
  - user: any
    ips: ['127.0.0.1']
    permissions: [{action: publish}]
  - user: $user
    pass: $pass
    permissions: [{action: read}]
paths:
EOF
  for spec in "$@"; do
    local name=${spec%%=*} size=${spec#*=}
    cat >> "$f" <<EOF
  $name:
    runOnInit: ffmpeg -hide_banner -loglevel error -re -f lavfi -i testsrc2=size=${size}:rate=15 -c:v libx264 -preset ultrafast -tune zerolatency -profile:v baseline -g 30 -pix_fmt yuv420p -f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/$name
    runOnInitRestart: yes
EOF
  done
}

gateway_mtx_conf() { # file
  sed -e 's#authHTTPAddress: http://agent:9123/auth#authHTTPAddress: http://127.0.0.1:9123/auth#' \
      -e 's#^apiAddress: :9997#apiAddress: 127.0.0.1:19997#' \
      -e 's#^rtspAddress: :8554#rtspAddress: 127.0.0.1:18554#' \
      "$HERE/../../../gateway/mediamtx.yml" > "$1"
}

case "${1:-}" in
up)
  MTX=$2; W=$3; mkdir -p "$W"
  netns lan1 10.10.1.1 10.10.1.2
  netns site2 192.168.50.1 192.168.50.2
  netns site3 192.168.60.1 192.168.60.2
  # site2/site3 are separate networks: the cloud cannot open connections into them.
  iptables -I OUTPUT -d 192.168.50.0/24 -m conntrack --ctstate NEW -j DROP
  iptables -I OUTPUT -d 192.168.60.0/24 -m conntrack --ctstate NEW -j DROP

  camera_conf "$W/lan1cam.yml" cam 'Lan1-Cam-Pass9' main=1280x720 sub=480x270
  camera_conf "$W/site2cam.yml" admin 'Site2-Cam-Pass9' stream1=1280x720 stream2=480x270
  camera_conf "$W/nvr.yml" admin 'Nvr-Lab-Pass9' ch1_main=1280x720 ch1_sub=640x360 ch2_main=1280x720 ch2_sub=640x360
  gateway_mtx_conf "$W/gw-mtx.yml"

  ip netns exec lan1 "$MTX" "$W/lan1cam.yml" > "$W/lan1cam.log" 2>&1 &
  ip netns exec site2 "$MTX" "$W/site2cam.yml" > "$W/site2cam.log" 2>&1 &
  ip netns exec site2 "$MTX" "$W/gw-mtx.yml" > "$W/site2-gwmtx.log" 2>&1 &
  ip netns exec site3 "$MTX" "$W/nvr.yml" > "$W/nvr.log" 2>&1 &
  ip netns exec site3 "$MTX" "$W/gw-mtx.yml" > "$W/site3-gwmtx.log" 2>&1 &
  ip netns exec site3 python3 "$HERE/fake_onvif_nvr.py" 192.168.60.2 8080 8554 admin 'Nvr-Lab-Pass9' > "$W/onvif.log" 2>&1 &
  sleep 2
  echo "lab up"
  ;;
agent) # ns workdir python code cloud ca subnet
  NS=$2; W=$3; PY=$4; CODE=$5; CLOUD=$6; CA=$7; SUBNET=$8
  mkdir -p "$W/$NS-state"
  ip netns exec "$NS" env CLOUD_URL="$CLOUD" CLOUD_CA_FILE="$CA" ENROLLMENT_CODE="$CODE" STATE_DIR="$W/$NS-state" \
    ALLOWED_SUBNETS="$SUBNET" MEDIAMTX_API=http://127.0.0.1:19997 MEDIAMTX_RTSP=127.0.0.1:18554 \
    AUTH_LISTEN=127.0.0.1:9123 RELAY_USER=relay RELAY_PASS="relay-$NS-0123456789abcdef" \
    "$PY" "$AGENT_DIR/camerascope_gateway.py" > "$W/$NS-agent.log" 2>&1 &
  echo "agent started in $NS"
  ;;
down)
  for ns in lan1 site2 site3; do
    for pid in $(ip netns pids "$ns" 2>/dev/null); do kill "$pid" 2>/dev/null || true; done
    ip netns del "$ns" 2>/dev/null || true
    ip link del "v-$ns" 2>/dev/null || true
  done
  iptables -D OUTPUT -d 192.168.50.0/24 -m conntrack --ctstate NEW -j DROP 2>/dev/null || true
  iptables -D OUTPUT -d 192.168.60.0/24 -m conntrack --ctstate NEW -j DROP 2>/dev/null || true
  echo "lab down"
  ;;
*) echo "usage: $0 up|agent|down"; exit 1 ;;
esac
