#!/usr/bin/env bash
# Wait until the docker engine can answer, then exit 0. This exists for exactly one reason.
#
# `systemd --user` units CANNOT order themselves After= a SYSTEM unit. `docker.service` lives in
# the system manager; `brain-store.service` lives in the user manager. There is no ordering edge
# to declare between them, so at boot the user manager can, and on a cold WSL start usually does,
# reach `brain-store.service` before dockerd is accepting connections. Without this the store unit
# fails once at boot with "Cannot connect to the Docker daemon", and a unit that failed at boot is
# the same shape as no unit at all -- which is the gap this whole lane closes.
#
# So the ordering is enforced from inside the unit instead of between units. Poll, do not assume.
#
# WHY THIS SCRIPT ALSO WRITES A FILE (task 0345)
#
# The seconds this script waited is the single number the whole reboot test exists to produce, and
# until 0345 it existed in one place: one line, emitted once, into the user journal. Measured on
# this host 2026-08-17: that journal retains ~3.75h (the 60s `brain-health` sweep logged 636
# entries in the retained window and evicts a one-shot's single line), and
# `journalctl --user -u brain-store.service -b` already returned `-- No entries --` on the same
# boot the unit started in. So an unattended 03:00 restart read at 09:00 shows three green units
# and has silently lost the measurement -- a green from a test that stopped measuring.
#
# The fix is to stop depending on the journal. Every invocation appends one line to
# systemd/.cold-start-log, which nothing rotates. The line carries the boot_id and the systemd
# INVOCATION_ID so a reader can prove the record belongs to THIS boot's actual brain-store start
# and is not a stale entry or a hand run; `systemd/reboot-test` gate 6 enforces exactly that and
# refuses to read any line that fails it. Both outcomes are recorded, reachable and timeout,
# because a boot where the gate blew is the case most worth keeping.
#
# The write can never fail this script: it is called `|| true`, every command inside it is
# guarded, and the wait behaviour is byte-identical whether the file is writable or not. A
# supervision gate must not acquire a new way to fail in order to become observable.
#
# Usage: brain-docker-wait.sh [timeout_seconds]   (default 120)
#   BRAIN_COLD_START_LOG=<path>   override the log path (tests; unset in the unit)
set -euo pipefail

TIMEOUT="${1:-120}"
deadline=$(( SECONDS + TIMEOUT ))

# $REPO/store/bin/brain-docker-wait.sh -> $REPO. The unit calls this by absolute path, so this
# resolves under systemd exactly as it does from a shell.
COLD_LOG="${BRAIN_COLD_START_LOG:-}"
if [[ -z "$COLD_LOG" ]]; then
  _repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)" || _repo=""
  [[ -n "$_repo" ]] && COLD_LOG="$_repo/systemd/.cold-start-log"
fi

# One key=value line per invocation. Never fatal: see the header.
record() {
  local outcome="$1" waited="$2"
  [[ -n "$COLD_LOG" ]] || return 0

  local boot uptime_us ppid_comm
  boot="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null)" || boot=""
  # /proc/uptime, not systemd's *TimestampMonotonic: those are different clocks across a suspend,
  # so this is labelled uptime_us and is forensic context, not something to subtract from systemd's
  # numbers. The binding that actually identifies the record is invocation=, below.
  uptime_us="$(awk '{printf "%.0f", $1 * 1000000}' /proc/uptime 2>/dev/null)" || uptime_us=""
  # TRAP, measured 2026-08-17 while building this: INVOCATION_ID is INHERITED. A shell started
  # inside any unit or scope carries its parent's id (this host runs agents under
  # `claude-<n>.scope`, so a hand run from one arrives with a perfectly real INVOCATION_ID set).
  # "INVOCATION_ID is non-empty" therefore does NOT mean "systemd ran this". Only an exact match
  # against the target unit's live InvocationID does, which is what reboot-test gate 6 requires.
  # ppid_comm is recorded alongside because it is not inherited: `systemd` under ExecStartPre,
  # a shell name for a hand run.
  ppid_comm="$(cat "/proc/$PPID/comm" 2>/dev/null | tr -d ' ')" || ppid_comm=""

  mkdir -p "$(dirname "$COLD_LOG")" 2>/dev/null || return 0
  # `2>/dev/null` FIRST, then the append. Redirections are applied left to right, so with the
  # usual `>> "$f" 2>/dev/null` order a failing `>>` reports "Permission denied" on the stderr it
  # still has -- which under systemd is brain-store.service's journal. Measured 2026-08-18 against
  # a read-only log file: the script still exited 0, but it printed an error into the unit that a
  # reader would reasonably take for the store failing. Silence here is the point.
  printf 'wall=%s boot=%s uptime_us=%s ppid_comm=%s invocation=%s outcome=%s waited_s=%s budget_s=%s pid=%s\n' \
    "$(date -Is 2>/dev/null || echo '-')" "${boot:--}" "${uptime_us:--}" "${ppid_comm:--}" \
    "${INVOCATION_ID:--}" "$outcome" "$waited" "$TIMEOUT" "$$" \
    2>/dev/null >> "$COLD_LOG" || return 0

  # One line per boot plus the occasional hand run, so this is a runaway guard, not a rotation
  # policy. Trim via a temp file in the same directory and mv, so a reader never sees a half file.
  local n; n="$(wc -l < "$COLD_LOG" 2>/dev/null | tr -dc '0-9')" || n=""
  if [[ -n "$n" && "$n" -gt 500 ]]; then
    tail -n 400 "$COLD_LOG" 2>/dev/null > "$COLD_LOG.tmp.$$" \
      && mv -f "$COLD_LOG.tmp.$$" "$COLD_LOG" 2>/dev/null
    rm -f "$COLD_LOG.tmp.$$" 2>/dev/null
  fi
  return 0
}

while :; do
  if docker info >/dev/null 2>&1; then
    # stdout stays byte-identical: systemd/reboot-test and systemd/README.md both grep for this
    # exact wording, and the journal copy is still the better read when it has not rotated yet.
    echo "brain-docker-wait: docker engine reachable after ${SECONDS}s"
    record reachable "$SECONDS" || true
    exit 0
  fi
  if (( SECONDS >= deadline )); then
    echo "brain-docker-wait: docker engine did not answer within ${TIMEOUT}s. Refusing to pretend" >&2
    echo "brain-docker-wait: the store started. Check: systemctl status docker" >&2
    record timeout "$SECONDS" || true
    exit 1
  fi
  sleep 2
done
