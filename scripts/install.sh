#!/usr/bin/env bash
# SPARK installer for Linux. Safe to run again: it keeps an existing .env and never asks for
# anything that is already set up.
#
#   ./scripts/install.sh
#
# Order (docs/development.md, "Nothing public before the admin exists"):
#   1. Cosmos and FileBrowser start without a tunnel (init writes Cosmos's safe config first).
#   2. spark setup creates the owner account (it asks for the name and password itself).
#   3. Everything in COMPOSE_PROFILES starts, the tunnel included.
# Quick-tunnel mode starts its tunnel before step 2: setup needs the random address.
set -euo pipefail

cd "$(dirname "$0")/.."

say() { printf '==> %s\n' "$*"; }
warn() { printf '[warn] %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
ask() { # ask <question> <default>
  local answer
  read -r -p "$1 [$2]: " answer || true
  printf '%s' "${answer:-$2}"
}

check_docker() {
  command -v docker >/dev/null || die "Docker is not installed. See https://docs.docker.com/engine/install/"
  docker info >/dev/null 2>&1 || die "Cannot talk to Docker. Is it running, and are you in the 'docker' group? (sudo usermod -aG docker \"\$USER\", then log in again)"
  local version major minor
  version=$(docker compose version --short 2>/dev/null) || die "The Docker Compose plugin is missing (package docker-compose-plugin)."
  version=${version#v}
  major=${version%%.*}
  minor=${version#*.}
  minor=${minor%%.*}
  if (( major < 2 || (major == 2 && minor < 24) )); then
    die "Docker Compose $version is too old; SPARK needs 2.24 or newer."
  fi
}

# Reads one KEY=value from .env without running it as shell code; drops surrounding quotes.
env_value() {
  local value
  value=$(sed -n "s/^$1=//p" .env | tail -n 1)
  value=${value#[\"\']}
  printf '%s' "${value%[\"\']}"
}

set_env() { # set_env KEY value  (in .env, replacing the line)
  local key=$1 value=$2
  [[ $value != *$'\n'* ]] || die "$key cannot contain a line break."
  awk -v key="$key" -v value="$value" -F= '
    $1 == key { print key "=" value; found = 1; next } { print }
    END { if (!found) print key "=" value }' .env > .env.tmp
  mv .env.tmp .env
}

create_env() {
  say "Creating .env (your answers can be changed there later)"
  (umask 077 && cp .env.example .env)

  local data
  data=$(ask "Folder for SPARK's files (absolute path)" "$HOME/spark-data")
  [[ $data == /* ]] || die "Use an absolute path, e.g. $HOME/spark-data"
  set_env SPARK_DATA "$data"

  echo
  echo "How will people reach this SPARK?"
  echo "  1) Your own domain or a friend's tunnel token  (spark.yourdomain.com)"
  echo "  2) Only from this laptop, for trying it out    (http://spark.localhost)"
  echo "  3) A temporary public test address              (random trycloudflare.com)"
  case $(ask "Choose 1, 2 or 3" "2") in
    1)
      set_env SPARK_HOSTNAME "$(ask "Hostname" "spark.example.com")"
      local token
      read -r -s -p "Tunnel token (input hidden): " token; echo
      [[ -n $token ]] || die "A tunnel token is needed. The domain owner gets one with: spark tunnel new <name>"
      set_env TUNNEL_TOKEN "$token"
      set_env COMPOSE_PROFILES tunnel
      ;;
    2)
      set_env SPARK_HOSTNAME spark.localhost
      set_env SPARK_HTTP_PORT 80
      set_env COMPOSE_PROFILES ""
      ;;
    3)
      warn "Anyone who learns the random address can reach this SPARK while it runs."
      set_env SPARK_HOSTNAME ""
      set_env COMPOSE_PROFILES quick-tunnel
      ;;
    *) die "Please answer 1, 2 or 3." ;;
  esac

  local zone
  zone=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo UTC)
  set_env TZ "$(ask "Time zone" "$zone")"
  set_env COSMOS_SERVER_COUNTRY "$(ask "Your country code (Cosmos never geo-blocks it)" "IN")"
}

check_data_folder() {
  local data=$1
  mkdir -p "$data" || die "Cannot create $data"
  # FileBrowser runs as uid 1000 and creates each person's folder in users/.
  local folder owner
  for folder in users shared; do
    if [[ -d $data/$folder ]]; then
      owner=$(stat -c %u "$data/$folder")
      [[ $owner == 1000 ]] || warn "$data/$folder belongs to uid $owner; FileBrowser (uid 1000) may not write there. Fix: sudo chown 1000:1000 $data/$folder"
    fi
  done
}

port_in_use() {
  command -v ss >/dev/null || return 1
  ss -ltnH "( sport = :$1 )" | grep -q .
}

wait_healthy() { # wait_healthy <service>...
  local service id status tries
  for service in "$@"; do
    for tries in $(seq 1 60); do
      id=$(docker compose ps -q "$service")
      status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null || true)
      [[ $status == healthy || $status == running ]] && break
      (( tries < 60 )) || die "$service did not become healthy. See: docker compose logs $service"
      sleep 3
    done
  done
}

main() {
  [[ $(uname -s) == Linux ]] || die "This installer is for Linux."
  check_docker
  if [[ -f .env ]]; then
    say "Using the existing .env"
  else
    create_env
  fi

  local data hostname port profiles
  data=$(env_value SPARK_DATA)
  hostname=$(env_value SPARK_HOSTNAME)
  port=$(env_value SPARK_HTTP_PORT); port=${port:-8080}
  profiles=$(env_value COMPOSE_PROFILES)
  [[ $data == /* ]] || die "SPARK_DATA in .env must be an absolute path."
  check_data_folder "$data"
  if port_in_use "$port" && [[ -z $(docker compose ps -q cosmos 2>/dev/null) ]]; then
    die "Port $port is already used by another program. Change SPARK_HTTP_PORT in .env."
  fi

  say "Building the SPARK helper and starting Cosmos (no public access yet)"
  docker compose build init
  if [[ -n $hostname ]]; then
    docker compose up -d cosmos filebrowser
    wait_healthy cosmos filebrowser
  else
    # FileBrowser needs a hostname for its sign-in; without one only Cosmos runs.
    docker compose up -d cosmos
    wait_healthy cosmos
  fi
  if [[ ,$profiles, == *,quick-tunnel,* ]]; then
    docker compose up -d quick-tunnel
  fi

  say "Setting up (first run: choose the owner account name and password)"
  docker compose run --rm -e SPARK_ADMIN_USER -e SPARK_ADMIN_PASSWORD -e SPARK_ADMIN_EMAIL spark setup

  say "Starting everything"
  if [[ -n $hostname || ,$profiles, == *,quick-tunnel,* ]]; then
    docker compose up -d
  fi
  docker compose run --rm spark doctor || warn "spark doctor found problems (see above)."
}

main "$@"
