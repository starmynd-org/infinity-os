#!/usr/bin/env bash
# THE REACH CHECK for the phone surface. Task 0114 (V8a), 2026-08-18.
#
# What has to be true for a phone on the tailnet to load the console, and -- the half that
# matters more -- what must NOT be true. Run it, do not read the answer out of a report:
#
#     ./web/bin/phone-reach.sh                 # check the running console
#     CONSOLE_PORT=3103 ./web/bin/phone-reach.sh
#
# Exit 0 = the bind posture is one of the two SANCTIONED shapes. Exit 1 = a defect.
#
# ------------------------------------------------------------------ the decision this encodes
#
# The operator decided a TAILSCALE TAILNET on 2026-08-18. The phone and the host join a private
# WireGuard mesh and speak over 100.x. This is NOT Tailscale Funnel: Funnel publishes to the
# public internet and v1 refused it BY NAME in `web/MUST-NOT-BUILD.md` #11, correctly. A tailnet
# is its opposite and that distinction is the entire reason this is allowed.
#
# THE TAILNET IS THE AUTH BOUNDARY AND THERE IS NO OTHER ONE. The console has no login, no
# session identity and no user model; `web/actions.py` acts under one constant, OPERATOR. Anyone
# on the tailnet is the operator as far as this surface can tell. That is a device credential,
# not an authenticated application, and this script is checking the network boundary because the
# network boundary is the whole of it.
#
# ------------------------------------------------------------------ the two sanctioned shapes
#
#   A. TAILSCALE SERVE, bind stays 127.0.0.1  <- V7a's recommendation, and the safer one.
#      `tailscale serve` proxies from the tailnet to 127.0.0.1:3103. The listening socket never
#      leaves loopback, so if tailscaled stops, the surface stops being reachable rather than
#      staying up on a routable address. The port registry already records this exact pattern
#      for Paperclip on the VPS.
#
#   B. BIND THE TAILNET ADDRESS, e.g. CONSOLE_HOST=100.64.0.10
#      Available today with no tailscale CLI. Two costs, both real: the address is routable so
#      the socket is the boundary rather than a proxy, and a bind to a tailnet /32 FAILS AT BOOT
#      if tailscaled has not come up yet -- a supervised unit needs a restart policy or
#      net.ipv4.ip_nonlocal_bind, which is V7b's problem and is named here so it is not a
#      surprise there.
#
#   DEFECT. 0.0.0.0 (and `--host 0.0.0.0`, and `flask run` with no --host on some versions).
#      MEASURED on this host 2026-08-18, real sockets and real GETs, three binds x three routes:
#          bind 127.0.0.1      -> loopback REACHED  tailnet REFUSED  home-LAN REFUSED
#          bind 100.64.0.10 -> loopback REFUSED  tailnet REACHED  home-LAN REFUSED
#          bind 0.0.0.0        -> loopback REACHED  tailnet REACHED  home-LAN REACHED
#      0.0.0.0 is not a style preference here. It publishes an unauthenticated console onto
#      192.168.x.0/24, the operator's home LAN, where the tailnet is no longer the boundary and
#      every phone, TV and guest device on the wifi reaches every verb in ROOM_VERBS.
#
# ------------------------------------------------------------------ how this survives the V7 move
#
# It survives by being a check rather than a sentence. Re-run it on a remote server after the
# cutover: the addresses differ (the VPS's own 100.x) and the assertions do not. The one item it
# cannot check from here is the PORT REGISTRY ROW, which V7's definition of done requires BEFORE
# the first bind and which is the one item that cannot be corrected after the fact.

set -uo pipefail

# The port comes from the one configuration point, `web/host.py` (task 0168), so this check and
# the console it checks cannot drift apart. CONSOLE_PORT= still wins; host.py reads it.
PORT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && python3 -c 'from web import host; print(host.BIND_PORT)')"
rc=0
say() { printf '%s\n' "$*"; }
bad() { printf 'DEFECT  %s\n' "$*"; rc=1; }
ok()  { printf 'ok      %s\n' "$*"; }

command -v ss >/dev/null || { say "ss is not on PATH; this check needs it"; exit 2; }

say "=== the console's own socket, port ${PORT} ==="
LINES="$(ss -ltnH "sport = :${PORT}" 2>/dev/null || true)"
if [ -z "$LINES" ]; then
  say "nothing is listening on ${PORT}. Start the console, then re-run:"
  say "    BRAIN_PG_DB=brain_console ./web/bin/console"
  exit 2
fi
ss -ltnp "sport = :${PORT}" 2>/dev/null | sed 's/^/        /'

BINDS="$(printf '%s\n' "$LINES" | awk '{print $4}')"
for b in $BINDS; do
  addr="${b%:*}"; addr="${addr#\[}"; addr="${addr%\]}"
  case "$addr" in
    0.0.0.0|::|"*")
      bad "${b} is a WILDCARD bind. This publishes the console on every interface, including"
      bad "        the home LAN, where nothing authenticates it. Bind 127.0.0.1 and put"
      bad "        Tailscale Serve in front of it, or bind the tailnet address explicitly." ;;
    127.0.0.1|::1)
      ok "${b} is loopback. Sanctioned shape A: a phone reaches this ONLY through"
      say "        Tailscale Serve. Confirm on the host running tailscaled:"
      say "            tailscale serve status        # expect a proxy to 127.0.0.1:${PORT}"
      say "        With no Serve configured, this bind is correct AND the phone cannot reach it." ;;
    100.*|fd7a:115c:a1e0:*)
      ok "${b} is a TAILNET address. Sanctioned shape B: reachable from tailnet peers"
      say "        and from nowhere else. Two follow-ups V7b owns: the unit needs a restart"
      say "        policy because this bind fails while tailscaled is still starting, and the"
      say "        port registry row must already say vps-detached-service." ;;
    *)
      bad "${b} is neither loopback nor a tailnet address. Something routable and not private"
      bad "        is serving the console." ;;
  esac
done

say ""
say "=== every wildcard listener on this host (context, not a verdict on the console) ==="
W="$(ss -ltnH | awk '$4 ~ /^0\.0\.0\.0:/ || $4 ~ /^\[::\]:/ {print $4}' | sort -u)"
if [ -z "$W" ]; then ok "none"; else
  printf '%s\n' "$W" | sed 's/^/        /'
  say "        Each of these is reachable from the home LAN. They are not all this lane's, and"
  say "        this check does not fail on them: it prints them so the console's posture is read"
  say "        next to the host's rather than in isolation."
fi

say ""
say "=== the tailnet interface, as this host sees it ==="
if command -v tailscale >/dev/null 2>&1; then
  tailscale status 2>&1 | head -5 | sed 's/^/        /'
else
  say "        the tailscale CLI is NOT on PATH here, and that is not evidence of no tailnet."
  say "        On this laptop tailscaled runs on WINDOWS and WSL2 mirrored networking exposes"
  say "        the interface, so any check that shells \`tailscale status\` reports a false"
  say "        negative. Read the addresses instead:"
fi
ip -o addr show 2>/dev/null | awk '$4 ~ /^100\./ || $4 ~ /^fd7a:115c:a1e0:/ {print "        "$2" "$4}'
TS="$(ip -o addr show 2>/dev/null | awk '$4 ~ /^100\./ {print $4}' | head -1)"
[ -n "$TS" ] && ok "this host has a tailnet address: ${TS}" \
             || bad "no 100.x address on any interface: this host is not on the tailnet"

say ""
say "=== what this check CANNOT prove, stated rather than implied ==="
say "        It proves what this host's socket accepts. It does not prove a REMOTE peer"
say "        completes the handshake: that also needs the tailnet ACL to permit the phone->host"
say "        edge, and on this laptop it needs the Windows firewall to allow inbound on ${PORT}"
say "        for the Tailscale interface. Both need a second device and neither is checkable"
say "        from inside this host. The one-line proof from the phone itself:"
say "            open http://${TS%%/*}:${PORT}/queue in mobile Safari on the tailnet"
say "        and the one-line proof from any tailnet peer with a shell:"
say "            curl -sS -o /dev/null -w '%{http_code}\\n' http://${TS%%/*}:${PORT}/api/health"

exit $rc
