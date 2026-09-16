"""How a page leaves this machine, and the R3 problem that decides it.

`_system/runtime-location-contract.md` R3: for each always-on live surface with side effects,
**exactly one host is authoritative at a time**, and two hosts both driving the same Telegram bot
double-consume its updates. A live VPS Cortana bridge already drives that bot; measured on
2026-08-16, not assumed:

    $ ssh <vps> systemctl --user is-active brain-telegram-bridge
    active
    $ ssh <vps> curl -s http://127.0.0.1:3180/health
    {"ok":true,"mode":"live","lastPollOk":"2026-08-16T12:08:13.509Z",
     "consecutivePollFailures":0,"agentLaneEnabled":true}

That bridge polls `getUpdates` in a loop right now. **A local paging consumer that opened its own
Telegram connection would be a second driver and an R3 incident.**

So this module does not drive the bot. It hands the message to the one authoritative driver over
its existing loopback endpoint, `POST /send-urgent`, which already applies that surface's outbound
queue, rate limiting, review-surface footer, allowlisted chat and no-approvals boundary. Exactly
one process ever speaks to Telegram, and it is the same one that spoke to Telegram before this
lane existed. **Nothing is stopped, because nothing needed to be stopped once the local half stops
trying to be a driver.**

That is a transport decision with a real consequence and it is posted to crosstalk slot 6 rather
than made quietly. The two things it is NOT: it is not a different transport (same bot, same chat,
same vendor), and it is not a workaround for the gate (the gating happens in SQL before a flagged
payload reaches this process at all).

**`FileTransport` is not a fallback that pretends.** When the relay is unreachable the page still
lands in two places the operator already reads, and the send is reported as `relayed=False` so no
report can claim a Telegram delivery that did not happen.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

#: The bridge's loopback endpoint. On the VPS it is 127.0.0.1:3180; from here it needs
#:   ssh -N -L 3180:127.0.0.1:3180 <vps>
#: which is the tunnel `~/.swarm/notify.sh` already documents in its disabled lane 3.
RELAY_URL = os.environ.get("PAGING_RELAY_URL", "http://127.0.0.1:3180/send-urgent")

SWARM_HOME = Path(os.environ.get("SWARM_HOME", str(Path.home() / ".swarm")))
LOCAL_LOG = SWARM_HOME / "operator" / "pages.log"
WINDOWS_DROP = Path(os.environ.get("PAGING_WINDOWS_DROP",
                                   "/mnt/c/Users/you/Downloads/swarm-pages.txt"))


class Transport:
    name = "transport"

    def send(self, text: str, *, link: str = "") -> dict:
        raise NotImplementedError


class FileTransport(Transport):
    """The two lanes the operator already reads. Always runs, even when the relay works.

    A page that exists only as a Telegram message is a page with no record. This one is the
    record, and it is on ext4 plus a path Explorer can open, because the bus itself has to live
    on ext4 out of easy reach from Windows.

    `send` reports `delivered` as **at least one target actually written**, and names the ones it
    lost under `failed_targets`/`error`. It is deliberately not "both targets written": the
    Windows drop is legitimately absent on a host with no Windows, and failing there would
    quarantine the pager for working normally. Task 0125.
    """

    name = "file"

    def send(self, text: str, *, link: str = "") -> dict:
        import time
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        line = f"{stamp}  {text}" + (f"\n{link}" if link else "") + "\n"
        written, failed = [], []
        for target in (LOCAL_LOG, WINDOWS_DROP):
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                written.append(str(target))
            except OSError as exc:
                failed.append(f"{target}: {exc}")
        # `delivered` is what LANDED, and the two targets are not equals. `LOCAL_LOG` on ext4 is
        # the record -- `memory.PageMemory` reads that same file back as "what did I send" -- and
        # `WINDOWS_DROP` is a convenience path that is absent by design wherever there is no
        # Windows to drop into, which is every host in the VPS topology. So one target on disk is
        # a page that exists and can be retracted, and reporting it as undelivered would quarantine
        # the pager on its first normal page there. Zero targets is a page that exists nowhere, and
        # that is the case the old literal `True` hid, from this dict, from the `paged` log line,
        # and from `memory.record_page`, which is documented as being called only after a transport
        # reported a delivery.
        result = {"transport": "file", "delivered": bool(written), "targets": written}
        if failed:
            # A separate key, never mixed into `targets`: a caller counting `targets` must not
            # count a miss as a hit, and `error` is the field `consumer.py`'s `paged`/`retracted`
            # log lines already format, so a partial loss becomes visible without inventing a
            # second convention for it.
            result["failed_targets"] = failed
            result["error"] = (f"{len(failed)} of {len(failed) + len(written)} targets failed: "
                               + "; ".join(failed))
        return result


class RelayTransport(Transport):
    """Hand the message to the single authoritative Telegram driver. Never drive the bot.

    `POST /send-urgent {text, link}` enqueues on the bridge's own outbound queue and returns 202.
    This process holds no bot token, opens no `getUpdates` connection, and cannot become a second
    driver even if it is misconfigured, because there is nothing in it that knows how.
    """

    name = "relay"

    def __init__(self, url: str = RELAY_URL, timeout: float = 6.0):
        self.url, self.timeout = url, timeout

    def send(self, text: str, *, link: str = "") -> dict:
        body = json.dumps({"text": text, "link": link}).encode("utf-8")
        req = urllib.request.Request(self.url, data=body,
                                     headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return {"transport": "relay", "delivered": True, "status": resp.status,
                        "body": resp.read().decode("utf-8", "replace")[:200]}
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            return {"transport": "relay", "delivered": False, "error": str(exc)}


class DryRunTransport(Transport):
    name = "dryrun"

    def __init__(self):
        self.sent = []

    def send(self, text: str, *, link: str = "") -> dict:
        self.sent.append(text)
        return {"transport": "dryrun", "delivered": True, "text": text}


def relay_reachable(url: str = RELAY_URL, timeout: float = 3.0) -> bool:
    health = url.rsplit("/", 1)[0] + "/health"
    try:
        with urllib.request.urlopen(health, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def open_tunnel(vps: str, local_port: int = 3180, remote_port: int = 3180):
    """Open the ssh tunnel to the one authoritative driver. Returns the Popen, or None.

    Deliberately a helper and not something the consumer does implicitly: opening a tunnel is an
    operator-visible act on his machine, and a consumer that silently created network paths to a
    remote host would be doing exactly what the port registry rules exist to make visible.
    """
    if relay_reachable(f"http://127.0.0.1:{local_port}/send-urgent"):
        return None
    proc = subprocess.Popen(
        ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
         "-o", "ConnectTimeout=8", "-L", f"{local_port}:127.0.0.1:{remote_port}", vps],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc


def default_transports() -> list:
    """File always; relay when the one authoritative driver is reachable on loopback.

    Order matters: the durable local record is written first, so a page is recorded even if the
    relay hangs for its whole timeout and the process is killed in between.
    """
    ts = [FileTransport()]
    if relay_reachable():
        ts.append(RelayTransport())
    return ts
