#!/usr/bin/env bash
# End-to-end smoke test on a throwaway compose project (default spark-e2e). It installs SPARK in
# laptop-only mode without publishing any port, runs browser-like checks from inside the project
# network, backs up and restores, and finally removes only what this project created.
#
#   tests/smoke/run.sh                 # needs Docker and Compose 2.24+, about 3 minutes
#   SMOKE_PROJECT=spark-e2e2 tests/smoke/run.sh
set -euo pipefail

cd "$(dirname "$0")/../.."
project=${SMOKE_PROJECT:-spark-e2e}
[[ $project != spark ]] || { echo "error: never run the smoke test as project 'spark'." >&2; exit 2; }

work=$(mktemp -d)
export SMOKE_ENV=$work/smoke.env
cat > "$SMOKE_ENV" <<EOF
COMPOSE_PROJECT_NAME=$project
SPARK_DATA=$work/data
SPARK_HOSTNAME=spark.localhost
SPARK_HTTP_PORT=80
COMPOSE_PROFILES=
COSMOS_SERVER_COUNTRY=IN
EOF

dc() {
  docker compose -p "$project" --env-file "$SMOKE_ENV" \
    -f compose.yaml -f tests/smoke/compose.smoke.yaml "$@"
}
spark() { dc run --rm -T "$@"; }
flow() { spark -e SPARK_HOSTNAME -e OWNER -e OWNER_PASSWORD -e GUEST -e GUEST_PASSWORD \
  -e GUEST_INVITE_KEY --entrypoint python spark /smoke/flow.py "$@"; }

cleanup() {
  dc --profile '*' down --volumes --remove-orphans >/dev/null 2>&1 || true
  # init and FileBrowser create root- and uid-1000-owned folders in the data folder.
  docker run --rm -v "$work:/work" --entrypoint rm spark-cli:local -rf /work/data >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

failed=0
step() { printf '\n==> %s\n' "$*"; }
expect() { # expect <what> <command...>
  if "${@:2}"; then echo "[ok]   $1"; else echo "[fail] $1"; failed=1; fi
}
wait_healthy() {
  local service tries
  for service in "$@"; do
    for tries in $(seq 1 60); do
      [[ $(docker inspect -f '{{.State.Health.Status}}' "$(dc ps -q "$service")" 2>/dev/null) == healthy ]] && break
      (( tries < 60 )) || { echo "[fail] $service never became healthy"; dc logs "$service" | tail -20; exit 1; }
      sleep 3
    done
  done
}
password() { printf 'Sm0ke!%s' "$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')"; }

export SPARK_HOSTNAME=spark.localhost OWNER=owner GUEST=guest
OWNER_PASSWORD=$(password); GUEST_PASSWORD=$(password)
export OWNER_PASSWORD GUEST_PASSWORD

step "Install ($project)"
dc build init
dc up -d cosmos filebrowser
wait_healthy cosmos filebrowser
spark -e SPARK_ADMIN_USER="$OWNER" -e SPARK_ADMIN_PASSWORD="$OWNER_PASSWORD" spark setup
wait_healthy cosmos filebrowser
expect "doctor passes after setup" spark spark doctor

step "Setup again changes nothing"
again=$(spark spark setup)
echo "$again"
expect "no changes on re-run" test "$(grep -c 'already up to date' <<<"$again")" -eq 2
expect "owner's Files rights left as they are" grep -q "already has Shared write access" <<<"$again"

step "Accounts and Files"
invite=$(spark spark user add "$GUEST")
GUEST_INVITE_KEY=$(grep -o 'key=[A-Za-z0-9]*' <<<"$invite" | cut -d= -f2)
export GUEST_INVITE_KEY
expect "invite link printed" test -n "$GUEST_INVITE_KEY"
expect "browser checks" flow

step "Backup and restore"
backup=$(spark spark backup)
echo "$backup"
archive=$(grep -o '/data/backups/spark-[0-9TZ]*\.tar\.gz' <<<"$backup")
expect "backup written" test -n "$archive"
spark spark user add later >/dev/null
spark spark restore "$archive" --yes
wait_healthy cosmos filebrowser
no_later_account() { ! spark spark user list | grep -q '^later '; }
expect "restore removed the account made after the backup" no_later_account
expect "doctor passes after restore" spark spark doctor
expect "owner still signs in to Files" flow first-sign-in

step "Result"
if (( failed )); then echo "SMOKE TEST FAILED"; exit 1; fi
echo "SMOKE TEST PASSED"
