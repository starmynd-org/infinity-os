#!/usr/bin/env bash
# Install the runtime's `systemd --user` units. Idempotent. Needs no sudo, and that is the point:
# sudo on this host requires a password no agent may enter, which is precisely why supervision
# here is `systemd --user` and not a system unit or a cron line owned by root.
#
# The units in this repo carry `@REPO@` rather than an absolute path, so the checked-in copy is
# host-portable the same way the VPS wrappers are (`NODE_BIN_DIR` per host via Environment=).
# This script is the substitution.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${SYSTEMD_USER_DIR:-$HOME/.config/systemd/user}"
UNITS=(brain-store.service brain-console.service brain-paging.service
       brain-health.service brain-health.timer
       brain-transcript-verify.service brain-transcript-verify.timer
       brain-transcript-backfill.service brain-transcript-backfill.timer
       brain-routine-tick.service brain-routine-tick.timer
       brain-intake-sweep.service brain-intake-sweep.timer)
# The two .service units behind timers are deliberately NOT enabled: a timer's Unit= starts its
# service, and enabling a oneshot alongside would also run it at every boot on top of the schedule.
ENABLE=(brain-store.service brain-console.service brain-paging.service
        brain-health.timer brain-transcript-verify.timer
        brain-transcript-backfill.timer brain-routine-tick.timer
        brain-intake-sweep.timer)

# THE n8n UNITS ARE OPT-IN. `brain-n8n.service` starts n8n through a launcher that lives OUTSIDE
# this repository, in the operator's brain checkout beside it, and `brain-n8n-failwatch` watches that
# n8n. This script used to install and enable both unconditionally, so every host without that
# checkout got a unit failing at every boot. Set INSTALL_N8N=1 on the host that has the launcher.
# Proved by `systemd/tests/test_install_n8n_opt_in.sh`.
N8N_UNITS=(brain-n8n.service brain-n8n-failwatch.service brain-n8n-failwatch.timer)
if [ "${INSTALL_N8N:-}" = "1" ]; then
  for u in "${N8N_UNITS[@]}"; do
    [ -f "$REPO/systemd/$u" ] || { echo "install.sh: INSTALL_N8N=1 but systemd/$u is not in this tree" >&2; exit 1; }
  done
  UNITS+=("${N8N_UNITS[@]}")
  ENABLE+=(brain-n8n.service brain-n8n-failwatch.timer)
fi

command -v systemctl >/dev/null || { echo "install.sh: no systemctl. Is systemd pid 1?" >&2; exit 1; }
[ "$(ps -p 1 -o comm=)" = "systemd" ] || {
  echo "install.sh: pid 1 is not systemd. On WSL, set [boot] systemd=true in /etc/wsl.conf." >&2
  exit 1; }

mkdir -p "$DEST"
for u in "${UNITS[@]}"; do
  sed "s|@REPO@|$REPO|g" "$REPO/systemd/$u" > "$DEST/$u"
  echo "installed $DEST/$u"
done
if [ "${INSTALL_N8N:-}" != "1" ] && [ -f "$DEST/brain-n8n.service" ]; then
  # An earlier install put it there. Leave it alone rather than disable something a host may use.
  echo "install.sh: $DEST/brain-n8n.service is from an earlier install and was left as it is (INSTALL_N8N is not 1)" >&2
fi

# Without linger the user manager is torn down when the last session closes and is not started at
# boot at all, so every unit below would wait for the operator to open a shell -- which defeats
# the entire point on an always-on box. This needs no sudo for one's own user.
loginctl enable-linger "$USER" || echo "install.sh: enable-linger failed; units will NOT survive a reboot" >&2
echo "linger: $(loginctl show-user "$USER" --property=Linger)"

systemctl --user daemon-reload
systemctl --user enable "${ENABLE[@]}"
echo
echo "enabled. Start them with:"
echo "  systemctl --user start ${ENABLE[*]}"
echo "Sweep by hand any time:  $REPO/systemd/brain-health"
echo "                         $REPO/systemd/brain-transcript-verify"
