#!/usr/bin/env bash
# Keep the laptop serving: ignore the lid switch and start Docker at boot. Needs root.
#
#   sudo ./scripts/always-on.sh             # apply
#   sudo ./scripts/always-on.sh --no-sleep  # also never suspend or hibernate
#   sudo ./scripts/always-on.sh --undo      # put the defaults back
#   ./scripts/always-on.sh --dry-run [...]  # only print what would be done
#
# The lid setting is a logind drop-in file, so the distribution's own logind.conf stays as it
# is. It takes effect after the next reboot: restarting logind now would end the desktop session.
set -euo pipefail

DROPIN=/etc/systemd/logind.conf.d/50-spark-lid.conf
SLEEP_TARGETS=(sleep.target suspend.target hibernate.target hybrid-sleep.target)

dry_run=false no_sleep=false undo=false
for arg in "$@"; do
  case $arg in
    --dry-run) dry_run=true ;;
    --no-sleep) no_sleep=true ;;
    --undo) undo=true ;;
    *) echo "usage: $0 [--no-sleep] [--undo] [--dry-run]" >&2; exit 2 ;;
  esac
done

run() {
  echo "+ $*"
  [[ $dry_run == true ]] || "$@"
}

write_dropin() {
  echo "+ write $DROPIN"
  [[ $dry_run == true ]] && return
  mkdir -p "$(dirname "$DROPIN")"
  cat > "$DROPIN" <<'EOF'
# Written by SPARK's scripts/always-on.sh: keep serving with the lid closed.
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
EOF
}

if [[ $dry_run == false && $(id -u) != 0 ]]; then
  echo "error: run it with sudo (or add --dry-run to only see what it does)." >&2
  exit 1
fi
command -v systemctl >/dev/null || { echo "error: this needs a systemd-based Linux." >&2; exit 1; }

if [[ $undo == true ]]; then
  run rm -f "$DROPIN"
  run systemctl unmask "${SLEEP_TARGETS[@]}"
  echo "Defaults are back after the next reboot. Docker still starts at boot."
  exit 0
fi

write_dropin
run systemctl enable docker.service containerd.service
if [[ $no_sleep == true ]]; then
  run systemctl mask "${SLEEP_TARGETS[@]}"
fi
echo "Done. Closing the lid will not suspend the laptop after the next reboot."
echo "Containers with restart policies (all of SPARK's) come back by themselves after a reboot."
