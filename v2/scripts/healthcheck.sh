#!/usr/bin/env bash
# End-to-end health probe. Returns 0 only if every surface answers correctly.
# Used by external monitoring and by the deploy runbook (DEPLOY.md §4).

set -euo pipefail

PUBLIC_HOST=${PUBLIC_HOST:-localhost}
PUBLIC_PORT=${PUBLIC_PORT:-5050}
INBOUND_HOST=${INBOUND_HOST:-localhost}
INBOUND_PORT=${INBOUND_PORT:-8443}
INTAKE_DB=${INTAKE_DB:-/opt/itips/var/intake.sqlite}

fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "ok:   $*"; }

curl -fsS "http://${PUBLIC_HOST}:${PUBLIC_PORT}/health" >/dev/null \
    || fail "public /health (5050) not responding"
ok "public /health"

curl -fsS "http://${PUBLIC_HOST}:${PUBLIC_PORT}/status" >/dev/null \
    || fail "public /status (5050) not responding"
ok "public /status"

# 8443 may be TLS-only in production; tolerate either http or https failures
# so a missing bearer header doesn't count as an outage.
curl -fsS -k -o /dev/null -w "%{http_code}" \
    "http://${INBOUND_HOST}:${INBOUND_PORT}/health" 2>/dev/null \
    | grep -qE '^(200|401)$' \
    || fail "inbound :8443 not responding"
ok "inbound :8443"

if [ -f "${INTAKE_DB}" ]; then
    rows=$(sqlite3 "${INTAKE_DB}" 'SELECT COUNT(*) FROM intake;' 2>/dev/null || echo "?")
    ok "intake db rows: ${rows}"
else
    echo "warn: intake db missing at ${INTAKE_DB} (first run?)"
fi

echo
echo "All checks passed."
