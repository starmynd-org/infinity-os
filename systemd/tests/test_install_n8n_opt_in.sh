#!/usr/bin/env bash
# `systemd/install.sh` must not install or enable the n8n units unless asked to. Carried item A4.
#
# THE DEFECT. `brain-n8n.service` starts n8n through a launcher script that lives OUTSIDE this
# repository, in the operator's own brain checkout beside it (`@REPO@/../<brain>/...`). install.sh
# installed and enabled it, and `brain-n8n-failwatch.timer` with it, unconditionally. On every host
# that is not the authoring laptop the launcher does not exist, so a fresh install carried a unit
# that fails at every boot, and INSTALL.md had to tell each reader to disable it by hand.
#
# THE RULE. A default install installs and enables neither. `INSTALL_N8N=1` restores both, for the
# one host whose brain checkout carries the launcher.
#
# HOW IT RUNS WITHOUT SYSTEMD. `systemctl`, `loginctl` and `ps` are replaced by stubs on PATH that
# record their arguments, and `SYSTEMD_USER_DIR` points the unit copies at a temp directory. So this
# needs no systemd, no user manager and no database, and it never touches a real unit.
#
# EVERY ABSENCE HAS A PRESENCE BESIDE IT FROM THE SAME RUN. "no brain-n8n in the enable line" is
# also what an install that enabled nothing prints, so each run also requires `brain-console.service`
# to be installed and enabled, and the opt-in run requires the n8n units to appear.
#
# Run: bash systemd/tests/test_install_n8n_opt_in.sh      Exit 0 all passed, 1 a case failed.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$(dirname "$HERE")")"
INSTALL="$ROOT/systemd/install.sh"
PASS=0
FAIL=0

ok()  { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }

[ -f "$INSTALL" ] || { printf 'test_install_n8n_opt_in: %s is missing\n' "$INSTALL" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/bin"
printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$*" >> "$STUB_LOG"\nexit 0\n' > "$WORK/bin/systemctl"
printf '#!/usr/bin/env bash\nprintf "Linger=yes\\n"\nexit 0\n' > "$WORK/bin/loginctl"
printf '#!/usr/bin/env bash\nprintf "systemd\\n"\nexit 0\n' > "$WORK/bin/ps"
chmod +x "$WORK/bin/systemctl" "$WORK/bin/loginctl" "$WORK/bin/ps"

# run_install <label> [VAR=value ...]: installs into $WORK/<label>/units, logs systemctl to
# $WORK/<label>/systemctl.log, prints the script's own output to $WORK/<label>/out.
run_install() {
  local label="$1"; shift
  mkdir -p "$WORK/$label/units"
  : > "$WORK/$label/systemctl.log"
  env "$@" USER="${USER:-tester}" SYSTEMD_USER_DIR="$WORK/$label/units" STUB_LOG="$WORK/$label/systemctl.log" \
    PATH="$WORK/bin:$PATH" bash "$INSTALL" > "$WORK/$label/out" 2>&1
  echo $?
}

enable_line() { grep -E '^--user enable ' "$WORK/$1/systemctl.log" | tail -1; }

printf '\n--- a default install\n'
rc="$(run_install default INSTALL_N8N=)"
[ "$rc" = "0" ] && ok "a default install exits 0" || bad "a default install exits 0" "exit $rc: $(tail -3 "$WORK/default/out")"

# PRESENCE FIRST: the install did install and enable something.
[ -f "$WORK/default/units/brain-console.service" ] \
  && ok "a default install installs brain-console.service (control)" \
  || bad "a default install installs brain-console.service (control)" "units: $(ls "$WORK/default/units" | tr '\n' ' ')"
case "$(enable_line default)" in
  *brain-console.service*) ok "a default install enables brain-console.service (control)" ;;
  *) bad "a default install enables brain-console.service (control)" "enable line: [$(enable_line default)]" ;;
esac
if grep -q '@REPO@' "$WORK/default/units/brain-console.service" 2>/dev/null; then
  bad "installed units carry the repo path, not @REPO@ (control)"
else
  ok "installed units carry the repo path, not @REPO@ (control)"
fi

# THE RULE.
n8n_files="$(ls "$WORK/default/units" | grep -c '^brain-n8n' || true)"
[ "$n8n_files" = "0" ] \
  && ok "a default install does not install brain-n8n.service or brain-n8n-failwatch" \
  || bad "a default install does not install brain-n8n.service or brain-n8n-failwatch" "installed: $(ls "$WORK/default/units" | grep '^brain-n8n' | tr '\n' ' ')"
case "$(enable_line default)" in
  *brain-n8n*) bad "a default install does not enable brain-n8n" "enable line: [$(enable_line default)]" ;;
  *) ok "a default install does not enable brain-n8n" ;;
esac
# The `installed <path>` lines are covered by the file check above; this reads the start command
# the script prints for the reader to paste.
if grep -v '^installed ' "$WORK/default/out" | grep -q 'brain-n8n'; then
  bad "a default install does not tell the reader to start brain-n8n" "$(grep -v '^installed ' "$WORK/default/out" | grep 'brain-n8n' | head -2)"
else
  ok "a default install does not tell the reader to start brain-n8n"
fi
count="$(ls "$WORK/default/units" | wc -l | tr -d ' ')"
[ "$count" = "13" ] && ok "a default install installs 13 unit files" || bad "a default install installs 13 unit files" "got $count"

printf '\n--- INSTALL_N8N=1\n'
rc="$(run_install optin INSTALL_N8N=1)"
# TWO TREES, TWO CORRECT ANSWERS. This repository ships the n8n units; the public export does not (they
# launch a script from the operator's brain checkout). Where the files are present the opt-in installs
# them; where they are absent the opt-in must refuse by name rather than install a half set.
if [ -f "$ROOT/systemd/brain-n8n.service" ]; then
  [ "$rc" = "0" ] && ok "an opt-in install exits 0" || bad "an opt-in install exits 0" "exit $rc: $(tail -3 "$WORK/optin/out")"
  for u in brain-n8n.service brain-n8n-failwatch.service brain-n8n-failwatch.timer; do
    [ -f "$WORK/optin/units/$u" ] && ok "INSTALL_N8N=1 installs $u" || bad "INSTALL_N8N=1 installs $u"
  done
  case "$(enable_line optin)" in
    *brain-n8n.service*brain-n8n-failwatch.timer*|*brain-n8n-failwatch.timer*brain-n8n.service*)
      ok "INSTALL_N8N=1 enables brain-n8n.service and brain-n8n-failwatch.timer" ;;
    *) bad "INSTALL_N8N=1 enables brain-n8n.service and brain-n8n-failwatch.timer" "enable line: [$(enable_line optin)]" ;;
  esac
  count="$(ls "$WORK/optin/units" | wc -l | tr -d ' ')"
  [ "$count" = "16" ] && ok "an opt-in install installs 16 unit files" || bad "an opt-in install installs 16 unit files" "got $count"
else
  [ "$rc" != "0" ] && ok "with the n8n units absent, INSTALL_N8N=1 refuses" || bad "with the n8n units absent, INSTALL_N8N=1 refuses" "exit $rc"
  grep -q 'is not in this tree' "$WORK/optin/out" \
    && ok "the refusal names the missing unit" \
    || bad "the refusal names the missing unit" "$(tail -3 "$WORK/optin/out")"
  count="$(ls "$WORK/optin/units" | wc -l | tr -d ' ')"
  [ "$count" = "0" ] && ok "a refused opt-in installs nothing" || bad "a refused opt-in installs nothing" "got $count"
fi

printf '\n%s passed, %s failed\n' "$PASS" "$FAIL"
[ "$PASS" -gt 0 ] || { printf 'DENOMINATOR: 0 checks passed and 0 is not a pass\n'; exit 1; }
[ "$FAIL" -eq 0 ]
