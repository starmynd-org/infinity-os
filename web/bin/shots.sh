#!/usr/bin/env bash
# Render the console with a real browser and write PNGs.
#
# The design guide's verification section closes with "Not verified: rendered appearance", because
# the Browser pane that measured everything else would not composite frames. It can be verified on
# this host: Playwright's chromium is already on disk and headless screenshots work, so density and
# rhythm are looked at rather than inferred from a DOM.
set -euo pipefail
CHROME="${CHROME:-$HOME/.cache/ms-playwright/chromium-1223/chrome-linux/chrome}"
# The address is asked for, not spelled: `web/host.py` is the one configuration point (task
# 0168), so a screenshot run and a phone check can never disagree about which console they
# measured. `BASE=` in the environment still wins, and host.py honours it.
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && python3 -c 'from web import host; print(host.BASE_URL)')"
OUT="${OUT:-/tmp/console-shots}"
mkdir -p "$OUT"

shot() { # name url width height theme
  local name="$1" url="$2" w="$3" h="$4" theme="${5:-dark}"
  local sep='?'; [[ "$url" == *"?"* ]] && sep='&'
  "$CHROME" --headless --disable-gpu --no-sandbox --hide-scrollbars \
    --screenshot="$OUT/$name.png" --window-size="$w,$h" --virtual-time-budget=2500 \
    "$BASE$url${sep}theme=$theme" 2>/dev/null || true
  printf '%-34s %s\n' "$name.png" "$(stat -c%s "$OUT/$name.png" 2>/dev/null || echo MISSING)"
}

W=1440; N=390
shot wide-queue-decide      "/queue"                        $W 1250 dark
shot wide-queue-judge-2pane "/queue?tier=judge"             $W 1250 dark
shot wide-queue-deepwork    "/queue?tier=judge&deep=1"      $W 1250 dark
shot wide-queue-open-review "/queue?tier=judge&open=0001"   $W 2100 dark
shot wide-stack             "/queue/stack?from=judge"       $W 900  dark
shot wide-brief             "/brief"                        $W 1500 dark
shot wide-fleet             "/fleet"                        $W 1100 dark
shot wide-scope             "/scope"                        $W 1100 dark
shot wide-study             "/study"                        $W 1700 dark
shot wide-task-review       "/task/0001"                    $W 2100 dark
shot wide-task-trail        "/task/0002"                    $W 1900 dark
shot light-queue-judge      "/queue?tier=judge"             $W 1250 light
shot light-brief            "/brief"                        $W 1500 light
shot light-task-review      "/task/0001"                    $W 2100 light

shot m390-queue-decide      "/queue"                        $N 1500 dark
shot m390-queue-judge       "/queue?tier=judge"             $N 1400 dark
shot m390-queue-deepwork    "/queue?tier=judge&deep=1"      $N 1200 dark
shot m390-brief             "/brief"                        $N 2000 dark
shot m390-study             "/study"                        $N 2200 dark
shot m390-task-review       "/task/0001"                    $N 2400 dark
shot m390-stack             "/queue/stack"                  $N 900  dark
shot m390-light-queue       "/queue"                        $N 1500 light
shot m375-queue             "/queue"                        375 1500 dark
