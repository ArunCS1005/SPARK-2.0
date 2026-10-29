#!/usr/bin/env bash
# Stops and removes SPARK's containers. Keeps accounts and settings (Docker volumes) unless
# --purge is given, and never touches the files in SPARK_DATA.
#
#   ./scripts/uninstall.sh            # containers only; ./scripts/install.sh brings it all back
#   ./scripts/uninstall.sh --purge    # also accounts, settings and the spark-cli image
set -euo pipefail

cd "$(dirname "$0")/.."

purge=false
case ${1:-} in
  "") ;;
  --purge) purge=true ;;
  *) echo "usage: $0 [--purge]" >&2; exit 2 ;;
esac

if [[ $purge == true ]]; then
  echo "This deletes every SPARK account, password and setting (the Docker volumes of this"
  echo "project). Files in SPARK_DATA stay. Make a backup first: docker compose run --rm spark backup"
  read -r -p "Type delete to continue: " answer || true
  [[ $answer == delete ]] || { echo "Nothing removed."; exit 1; }
  # Only this project's containers, networks and volumes; images other projects may share stay.
  docker compose --profile '*' down --volumes --remove-orphans
  docker image rm spark-cli:local 2>/dev/null || true
  echo "SPARK removed. Your files are still in SPARK_DATA; .env is still in this folder."
else
  docker compose --profile '*' down --remove-orphans
  echo "SPARK stopped and its containers removed. Accounts and settings are kept."
fi
