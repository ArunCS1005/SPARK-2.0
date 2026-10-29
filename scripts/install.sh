#!/usr/bin/env bash
# SPARK installer for Linux. Safe to run again: it keeps an existing .env, never asks for anything
# that is already set up, and prints the current address at the end (a temporary public address
# changes whenever the tunnel restarts, so running this again is also how you get the new one).
#
#   ./scripts/install.sh
#
# Order (docs/development.md, "Nothing public before the admin exists"):
#   1. Cosmos (and FileBrowser) start without a tunnel; init writes Cosmos's safe config first.
#   2. spark setup creates the owner account (it asks for the name and password itself).
#   3. Everything in COMPOSE_PROFILES starts, the tunnel included.
# The temporary public address is the exception: its tunnel starts before step 2, because setup
# needs the random address, and setup runs right after.
set -euo pipefail

cd "$(dirname "$0")/.."
LOG=$PWD/install.log

say() { printf '\n==> %s\n' "$*"; }
done_() { printf '    ✓ %s\n' "$*"; }
warn() { printf '[warn] %s\n' "$*" >&2; }
die() { printf '\nerror: %s\n' "$*" >&2; exit 1; }
ask() { # ask <question> <default>
  local answer
  read -r -p "$1 [$2]: " answer || true
  printf '%s' "${answer:-$2}"
}
# Runs a command with its output in install.log; shows the end of the log if it fails.
quiet() {
  if ! "$@" >>"$LOG" 2>&1; then
    tail -n 25 "$LOG" >&2
    die "'$*' failed. The full output is in $LOG"
  fi
}

check_docker() {
  command -v docker >/dev/null || die "Docker is not installed. See docs/guides/try-spark.md, step 1."
  docker info >/dev/null 2>&1 || die "Cannot talk to Docker. Is it running, and are you in the 'docker' group? (sudo usermod -aG docker \"\$USER\", then log out and in again)"
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
  say "A few questions (answers are saved in .env and can be changed there later)"
  (umask 077 && cp .env.example .env)

  local data
  data=$(ask "Folder for SPARK's files (absolute path)" "$HOME/spark-data")
  [[ $data == /* ]] || die "Use an absolute path, e.g. $HOME/spark-data"
  set_env SPARK_DATA "$data"

  echo
  echo "How will people reach this SPARK?"
  echo "  1) A temporary public address, for trying SPARK out   (random https://….trycloudflare.com)"
  echo "  2) Your own domain or a friend's tunnel token         (spark.yourdomain.com)"
  echo "  3) Only from this laptop                              (http://spark.localhost)"
  case $(ask "Choose 1, 2 or 3" "1") in
    1)
      warn "Anyone who learns the random address can open the sign-in page while SPARK runs."
      warn "Your accounts and passwords protect it; use it for testing, not for private files."
      set_env SPARK_HOSTNAME ""
      set_env COMPOSE_PROFILES quick-tunnel
      ;;
    2)
      set_env SPARK_HOSTNAME "$(ask "Hostname" "spark.example.com")"
      local token
      read -r -s -p "Tunnel token (input hidden): " token; echo
      [[ -n $token ]] || die "A tunnel token is needed. The domain owner gets one with: spark tunnel new <name>"
      set_env TUNNEL_TOKEN "$token"
      set_env COMPOSE_PROFILES tunnel
      ;;
    3)
      set_env SPARK_HOSTNAME spark.localhost
      set_env SPARK_HTTP_PORT 80
      set_env COMPOSE_PROFILES ""
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
      (( tries < 60 )) || die "$service did not start properly. See: docker compose logs $service"
      sleep 3
    done
  done
}

quick_tunnel_address() {
  docker compose logs --no-log-prefix quick-tunnel 2>/dev/null \
    | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -n 1
}

summary() { # summary <address> <quick: true|false>
  local address=$1 quick=$2
  printf '\n%s\n' "================================================================================"
  printf '  SPARK is ready\n\n'
  printf '  Dashboard:  %s\n' "$address"
  printf '  Files:      %s/files   (click "Sign in with SPARK")\n\n' "$address"
  printf '  Sign in with the owner account you created above.\n\n'
  printf '  Invite someone:  docker compose run --rm spark user add <name>\n'
  printf '  Health check:    docker compose run --rm spark doctor\n'
  printf '  Stop SPARK:      docker compose stop\n'
  if [[ $quick == true ]]; then
    printf '\n  This address is public and temporary: it changes when the laptop or Docker\n'
    printf '  restarts. Run ./scripts/install.sh again to get the new one.\n'
  fi
  printf '%s\n' "================================================================================"
}

main() {
  [[ $(uname -s) == Linux ]] || die "This installer is for Linux."
  [[ $EUID != 0 ]] || die "Run it as your normal user, without sudo (you need to be in the 'docker' group)."
  : > "$LOG"
  say "Checking Docker"
  check_docker
  done_ "Docker and Compose are ready"
  if [[ -f .env ]]; then
    done_ "Using your existing settings in .env"
  else
    create_env
  fi

  local data hostname port profiles quick=false
  data=$(env_value SPARK_DATA)
  hostname=$(env_value SPARK_HOSTNAME)
  port=$(env_value SPARK_HTTP_PORT); port=${port:-8080}
  profiles=$(env_value COMPOSE_PROFILES)
  [[ ,$profiles, == *,quick-tunnel,* ]] && quick=true
  [[ $data == /* ]] || die "SPARK_DATA in .env must be an absolute path."
  check_data_folder "$data"
  if port_in_use "$port" && [[ -z $(docker compose ps -q cosmos 2>/dev/null) ]]; then
    die "Port $port is already used by another program. Change SPARK_HTTP_PORT in .env."
  fi

  say "Preparing SPARK (the first time this downloads about 500 MB and takes a few minutes)"
  quiet docker compose build init
  done_ "SPARK helper built"
  if [[ -n $hostname ]]; then
    quiet docker compose up -d --quiet-pull cosmos filebrowser
    wait_healthy cosmos filebrowser
  elif [[ $quick == true ]]; then
    # FileBrowser restarts until setup gives it the public address, then setup configures it.
    quiet docker compose up -d --quiet-pull cosmos filebrowser
    wait_healthy cosmos
  else
    # Without any address FileBrowser cannot sign anyone in, so only the dashboard runs.
    quiet docker compose up -d --quiet-pull cosmos
    wait_healthy cosmos
  fi
  done_ "Dashboard started (not public yet)"
  if [[ $quick == true ]]; then
    quiet docker compose up -d --quiet-pull quick-tunnel
    done_ "Public address requested from Cloudflare"
  fi

  say "Setting up SPARK (the first time you choose the owner account)"
  docker compose run --rm -e SPARK_ADMIN_USER -e SPARK_ADMIN_PASSWORD -e SPARK_ADMIN_EMAIL spark setup

  say "Starting everything"
  if [[ -n $hostname || $quick == true ]]; then
    quiet docker compose up -d --quiet-pull
    wait_healthy cosmos filebrowser
  fi
  done_ "All services running"
  if docker compose run --rm spark doctor >>"$LOG" 2>&1; then
    done_ "Health check passed"
  else
    warn "The health check found problems; see: docker compose run --rm spark doctor"
  fi

  local address
  if [[ $quick == true ]]; then
    address=$(quick_tunnel_address)
  elif [[ $hostname == *.localhost ]]; then
    address=http://$hostname
  elif [[ -n $hostname ]]; then
    address=https://$hostname
  else
    address=http://localhost:$port
  fi
  summary "$address" "$quick"
}

main "$@"
