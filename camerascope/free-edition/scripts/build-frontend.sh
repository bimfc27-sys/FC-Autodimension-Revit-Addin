#!/usr/bin/env bash
# Builds the Free Edition PWA (static export of ../frontend) into ./public for Cloudflare Pages.
set -euo pipefail
cd "$(dirname "$0")/.."
FE=../frontend
( cd "$FE" && npm ci --no-audit --no-fund >/dev/null && NEXT_TELEMETRY_DISABLED=1 NEXT_PUBLIC_EDITION=free npx next build )
rm -rf public && cp -r "$FE/out" public
cp static/_headers public/_headers
echo "Free Edition frontend built into $(pwd)/public"
