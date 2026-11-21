#!/usr/bin/env bash
# Deploys CameraScope Free Edition to Cloudflare (free plan): D1 database, migrations,
# Pages project with Functions, and the CREDENTIAL_KEY secret.
#
# Needs: CLOUDFLARE_API_TOKEN (Account: Cloudflare Pages Edit, D1 Edit, Account Settings Read)
#        CLOUDFLARE_ACCOUNT_ID (optional: detected from the token)
#        PROJECT_NAME (optional, default "camerascope")
# Idempotent: safe to run again for updates. The credential key is created ONCE and never rotated
# automatically (rotating it would make stored camera passwords unreadable).
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT=${PROJECT_NAME:-camerascope}
DB=camerascope
WR="npx wrangler"
api() { curl -fsS -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" "https://api.cloudflare.com/client/v4$1"; }

if [ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
  CLOUDFLARE_ACCOUNT_ID=$(api /accounts | python3 -c "import json,sys; r=json.load(sys.stdin)['result']; print(r[0]['id'] if r else '')")
  [ -n "$CLOUDFLARE_ACCOUNT_ID" ] || { echo "No Cloudflare account visible to this token" >&2; exit 1; }
  export CLOUDFLARE_ACCOUNT_ID
fi
echo "==> account ${CLOUDFLARE_ACCOUNT_ID:0:6}…"

echo "==> D1 database"
DB_ID=$(api "/accounts/$CLOUDFLARE_ACCOUNT_ID/d1/database?name=$DB" | python3 -c "import json,sys; r=[d for d in json.load(sys.stdin)['result'] if d['name']=='$DB']; print(r[0]['uuid'] if r else '')")
if [ -z "$DB_ID" ]; then
  $WR d1 create "$DB" >/dev/null
  DB_ID=$(api "/accounts/$CLOUDFLARE_ACCOUNT_ID/d1/database?name=$DB" | python3 -c "import json,sys; print([d for d in json.load(sys.stdin)['result'] if d['name']=='$DB'][0]['uuid'])")
fi
cp -n wrangler.toml.example wrangler.toml 2>/dev/null || true
sed -i.bak -E "s/^database_id = \"[^\"]*\"/database_id = \"$DB_ID\"/" wrangler.toml && rm -f wrangler.toml.bak
sed -i.bak -E "s/^name = \"[^\"]*\"/name = \"$PROJECT\"/" wrangler.toml && rm -f wrangler.toml.bak
echo "    database $DB_ID"

echo "==> migrations"
$WR d1 migrations apply "$DB" --remote

echo "==> Pages project"
if ! api "/accounts/$CLOUDFLARE_ACCOUNT_ID/pages/projects/$PROJECT" >/dev/null 2>&1; then
  $WR pages project create "$PROJECT" --production-branch main
fi
SUBDOMAIN=$(api "/accounts/$CLOUDFLARE_ACCOUNT_ID/pages/projects/$PROJECT" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['subdomain'])")

echo "==> credential encryption key (created once)"
HAS_KEY=$(api "/accounts/$CLOUDFLARE_ACCOUNT_ID/pages/projects/$PROJECT" | python3 -c "import json,sys; e=json.load(sys.stdin)['result'].get('deployment_configs',{}).get('production',{}).get('env_vars') or {}; print('yes' if 'CREDENTIAL_KEY' in e else 'no')")
if [ "$HAS_KEY" = "no" ]; then
  printf 'k1:%s' "$(head -c 32 /dev/urandom | base64)" | $WR pages secret put CREDENTIAL_KEY --project-name "$PROJECT" >/dev/null
  echo "    created"
else
  echo "    already present (kept)"
fi

echo "==> build + deploy"
[ -d public ] || bash scripts/build-frontend.sh
$WR pages deploy public --project-name "$PROJECT" --branch main --commit-dirty=true
echo
echo "CameraScope Free Edition: https://$SUBDOMAIN"
