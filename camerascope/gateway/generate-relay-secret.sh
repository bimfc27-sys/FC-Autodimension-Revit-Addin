#!/usr/bin/env sh
# Adds a random RELAY_PASS (used between the gateway's MediaMTX and the agent) to .env.
set -eu
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
if grep -q '^RELAY_PASS=' .env; then
  echo "RELAY_PASS already set in .env"
else
  printf 'RELAY_PASS=%s\n' "$(head -c 32 /dev/urandom | base64 | tr -d '\n=+/')" >> .env
  chmod 600 .env
  echo "RELAY_PASS added to .env"
fi
