#!/bin/sh
set -eu

echo "Starting Portfolio Hub..."
echo "FLASK_ENV=${FLASK_ENV:-production}"
echo "FLASK_DEBUG=${FLASK_DEBUG:-0}"
echo "PORT=${PORT:-5000}"

for var in SECRET_KEY FERNET_KEY CORE_DATABASE_URL; do
  eval "value=\${${var}:-}"
  if [ -z "$value" ]; then
    echo "ERROR: Required environment variable $var is not set" >&2
    exit 1
  fi
done

if [ -z "${TENANT_DATABASE_URL:-}" ]; then
  echo "TENANT_DATABASE_URL is not set. Tenant bind will reuse CORE_DATABASE_URL."
fi

web_runtime="false"
case " $* " in
  *gunicorn*) web_runtime="true" ;;
esac

if [ "${MALWARE_SCAN_REQUIRED:-true}" = "true" ]; then
  scanner="${MALWARE_SCANNER_COMMAND:-clamscan --no-summary}"
  executable="${scanner%% *}"
  if ! command -v "$executable" >/dev/null 2>&1; then
    echo "ERROR: Required malware scanner is unavailable: $executable" >&2
    exit 1
  fi
  # The Render pre-deploy command shares this entrypoint but never handles
  # uploads. Initialize the high-memory scanner only for the Gunicorn runtime.
  if [ "$executable" = "clamscan" ] && [ "$web_runtime" = "true" ]; then
    signature_dir="${MALWARE_SIGNATURE_DIRECTORY:-/var/lib/clamav}"
    update_interval="${CLAMAV_UPDATE_INTERVAL_SECONDS:-21600}"
    if ! freshclam --quiet --user=appuser --datadir="$signature_dir"; then
      if ! find "$signature_dir" -maxdepth 1 -type f \
        \( -name 'daily.cvd' -o -name 'daily.cld' \) -mtime -1 -print -quit \
        | grep -q .; then
        echo "ERROR: ClamAV signatures could not be refreshed and are stale." >&2
        exit 1
      fi
      echo "WARNING: Signature refresh failed; using signatures built in the last 24 hours." >&2
    fi
    if ! clamscan --no-summary /app/docker-entrypoint.sh >/dev/null 2>&1; then
      echo "ERROR: ClamAV startup self-test failed." >&2
      exit 1
    fi
    (
      while :; do
        sleep "$update_interval"
        freshclam --quiet --user=appuser --datadir="$signature_dir" \
          || echo "WARNING: Scheduled ClamAV signature refresh failed." >&2
      done
    ) &
  fi
fi

# Render runs the same operations as a pre-deploy command. Self-hosted Docker
# deployments may opt in here; the database lock makes concurrent attempts safe.
if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
  echo "Running and verifying core + tenant Alembic migrations..."
  flask db-upgrade-all
  echo "Ensuring default tenant/profile records..."
  flask ensure-default-tenant
fi

if [ "${CREATE_SUPERADMIN_ON_STARTUP:-false}" = "true" ]; then
  echo "Ensuring SuperAdmin account..."
  flask create-superadmin
fi

echo "Launching app..."
exec "$@"
