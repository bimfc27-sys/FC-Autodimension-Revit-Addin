#!/usr/bin/env sh
# Creates .env from .env.example with strong random secrets.
set -eu
cd "$(dirname "$0")/.."
if [ -f .env ]; then
  echo ".env already exists - refusing to overwrite (delete it first to regenerate)." >&2
  exit 1
fi
rand() { head -c "$1" /dev/urandom | base64 | tr -d '\n'; }
pg=$(rand 24 | tr '+/' '-_' | tr -d '=')
jwt=$(rand 48 | tr '+/' '-_' | tr -d '=')
key=$(rand 32)
sed -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${pg}|" \
    -e "s|^JWT_SECRET=.*|JWT_SECRET=${jwt}|" \
    -e "s|^CREDENTIAL_KEYS=.*|CREDENTIAL_KEYS=k$(date +%Y%m%d):${key}|" \
    .env.example > .env
chmod 600 .env
echo "Wrote .env (mode 600). Keep CREDENTIAL_KEYS safe: losing it makes stored camera credentials unrecoverable."
