"""The one thing every connector shares: how to push to the intake door.

WHY THIS FILE IS THE ONLY THING CONNECTORS IMPORT FROM THE REPO, and it is a rule rather than a
convenience. `knowledge/infinity-os-product/playbooks/adding-a-connector` opens with *"write the
connector outside the core; the core must not grow a connector."* A connector that imports
`store`, or a transition, or anything from `engine/`, has grown into the core: it now needs a
database login, it breaks when an unrelated lane edits a verb, and it cannot be handed to a user
to run on their own machine.

So a connector in this directory may import **this module and the standard library, and nothing
else from this repository**. If a connector needs something the core has, the answer is to put it
behind the door's HTTP contract, not to import it. That constraint is what keeps every connector
deletable, and it is why the door was made a separate service in the first place
(`d-intake-separate-service`, decided by the operator 2026-09-01).

The contract this speaks is `intake-service/CONTRACT.md`. That file is the specification; this is
one client of it, and it is not privileged: a connector written in n8n, Make, Zapier or bash is
exactly as legitimate and gets exactly the same answers.

WHAT THIS DELIBERATELY DOES NOT DO.

* **It does not classify.** It does not summarise, re-title, prioritise or drop. A wrong
  classification is the same defect class as a fabricated transcription, because it will be
  believed later, and that argument is written into the intake transition itself.
* **It does not retry forever and it does not buffer to disk.** A `503` is raised to the caller.
  The connector is the queue: it still has the mailbox, the API and the file it read from, and a
  silent local buffer is how an intake system loses work it has already reported as taken.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request

#: Where the door is. The default is the registered port, and it is the SAME number `intake-service
#: /intake_service/host.py` defaults to -- deliberately duplicated here rather than imported,
#: because importing it would make every connector depend on the core it is supposed to stay out
#: of. An override exists so a connector can be pointed at a scratch door in a test.
DOOR_URL = os.environ.get("INTAKE_URL", "http://127.0.0.1:3106/intake")

#: A named User-Agent, and it is not decoration. On this host a valid credential was read as dead
#: because `Python-urllib/3.x` gets a 403 from Cloudflare where `requests`, `curl` and a browser
#: all get 200 -- measured against the tl;dv API on 2026-09-01. The door itself is loopback and
#: fronted by nothing, so this changes no outcome today; it is set so that the day a connector is
#: pointed at a door behind a proxy, the failure is not a mystery 403 on a working token.
USER_AGENT = "infinite-brain-connector/1"

#: The door's own limit, mirrored so a connector can truncate BEFORE the round trip rather than
#: learn about it from a 413. The door is the load-bearing copy; this one exists to be polite.
MAX_CONTENT_BYTES = int(os.environ.get("INTAKE_MAX_CONTENT_BYTES", str(256 * 1024)))


class DoorError(RuntimeError):
    """The door refused, and the connector must decide what that means.

    `status` is the HTTP status so a caller can tell the three cases apart without parsing prose:
    4xx means this payload is wrong and retrying it unchanged will fail identically forever;
    `503` means the store was unreachable and the SAME payload should be retried later; anything
    else is unclassified and should be treated as fatal for the run rather than silently skipped.
    """

    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"intake door returned {status}: {body[:400]}")

    @property
    def retryable(self) -> bool:
        return self.status == 503


#: Where the credential lives when it is not in the environment. Native ext4, mode 0600, never
#: `/mnt/c` and never in git -- the same arrangement `engine/bin/scratch-db.sh` uses for the
#: store's own secrets at `~/.brain-postgres-secrets`.
TOKEN_FILE = os.environ.get(
    "INTAKE_TOKEN_FILE",
    os.path.join(os.path.expanduser("~"), ".intake-service-secrets", "intake-token"),
)


def token() -> str:
    """The shared secret, or a refusal that says what to do.

    Fail closed and fail loudly: a connector that pushed without a token would get a 401 per item
    and could easily be read as "the door is down" when the door is fine and this side forgot its
    credential.

    TWO SOURCES, ENVIRONMENT FIRST, AND THE FILE IS NOT A CONVENIENCE. A connector run by hand has
    `INTAKE_TOKEN` exported. A connector run **on a schedule by n8n** does not: n8n's environment is
    fixed by `systemd/brain-n8n.service`, which is outside this lane's ownership, so a scheduled
    connector that could only read the environment would need a file this lane may not edit in
    order to run at all. Reading the same secret from disk keeps the cadence inside the lane and
    keeps the credential out of both the repo and the process table -- `INTAKE_TOKEN=... python3 ...`
    on a command line is visible to every process on the host in `ps`.

    An unreadable or empty file is treated as no credential, never as an empty one: an empty string
    compares equal to an empty header, which is the shape that turns a missing secret into an open
    door on the other side of the wire.
    """
    tok = os.environ.get("INTAKE_TOKEN", "").strip()
    if tok:
        return tok
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as fh:
            tok = fh.read().strip()
    except OSError:
        tok = ""
    if not tok:
        raise RuntimeError(
            "no credential for the intake door: INTAKE_TOKEN is unset or empty and "
            f"{TOKEN_FILE} is missing, unreadable or empty. It is the same value the service was "
            "started with; see intake-service/CONTRACT.md."
        )
    return tok


def stable_key(*parts: object) -> str:
    """A deterministic idempotency key from whatever identifies the item at its source.

    THE ONE RULE, AND IT IS THE DEFECT THIS REPO HAS ALREADY SHIPPED: nothing time-varying goes in
    here. The intake heartbeat wrote a fresh `new Date()` into its own file every five minutes, so
    the file's `size:mtime` signature changed on every tick and the signature half of the dedup
    never once fired -- leaving the objective-name check as the only thing between a five-minute
    schedule and 288 objectives a day, while the code comment claimed both were holding.

    Pass the source's own stable identifiers: a Gmail Message-ID, a Slack channel plus ts, a
    tl;dv meeting id. Never a clock, never a row number, never a sequence from this run.
    """
    joined = "\x1f".join("" if p is None else str(p) for p in parts)
    return "sha256:" + hashlib.sha256(joined.encode("utf-8")).hexdigest()


def push(*, source: str, timestamp: str, author: str, content: str,
         origin: str, title: str | None = None, idempotency_key: str | None = None,
         attachments: list | None = None, metadata: dict | None = None,
         url: str | None = None, timeout: float = 30.0) -> dict:
    """Push one canonical payload at the door and return its answer.

    Returns the parsed body. `deduped` is True when the door recognised the item and did NOT land
    a second objective -- which is a SUCCESS and not a failure. A connector that treats it as an
    error will loop.

    `origin` says whether a human or a machine produced the item, and it is not optional here even
    though the wire contract tolerates its absence, because an undeclared row reads as human and
    reaches the operator's attention badge. A machine that does not declare itself costs him a
    glance every day, forever. When a machine carries a human's words -- an email, a Slack
    message, a meeting transcript -- the origin is `human`: the person wrote it and the connector
    only moved it.
    """
    if origin not in ("human", "machine"):
        raise ValueError(
            f"origin must be 'human' or 'machine', got {origin!r}. It is refused rather than "
            "folded, because the fold is toward 'human' and a machine that meant to be quiet "
            "would silently start appearing on his badge instead."
        )

    payload = {
        "source": source,
        "timestamp": timestamp,
        "author": author,
        "content": content,
        "origin": origin,
    }
    if title:
        payload["title"] = title
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    if attachments:
        payload["attachments"] = attachments
    if metadata:
        payload["metadata"] = metadata

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url or DOOR_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token()}",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:      # the door answered, and said no
        raise DoorError(exc.code, exc.read().decode("utf-8", errors="replace")) from exc
    except urllib.error.URLError as exc:       # nothing answered at all
        raise DoorError(0, f"no answer from {url or DOOR_URL}: {exc.reason}") from exc
