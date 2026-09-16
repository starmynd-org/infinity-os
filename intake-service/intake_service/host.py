"""WHERE THE INTAKE DOOR IS SERVED, AND WHAT IT WILL LET THROUGH. One file, four values.

This mirrors `web/host.py` on purpose. That file exists because the console's address had been
spelled in five places and moving it meant finding all five; the same disease is cheaper to
prevent here than to cure, so the bind, the port and the credential are asked for once and copied
nowhere.

WHY THIS DOOR HAS NO ORIGIN GUARD, WHICH IS NOT AN OVERSIGHT. `web/guard.py` compares a browser's
`Origin` against the console's own, because the console authenticates writes with a COOKIE, and a
cookie is ambient authority that any page on the internet can make a browser spend. This door
authenticates with a bearer token in a header, which a browser never attaches on its own, so
there is no ambient authority to steal and CSRF does not reach it. Copying the origin check here
would be cargo cult, and worse, it would break every legitimate producer: n8n, Make, Zapier and
`curl` send no `Origin` at all.

WHAT PROTECTS IT INSTEAD, and both halves are needed:

    1. The token. Unset means the process REFUSES TO BOOT. There is no anonymous mode.
    2. The bind. Loopback by default, so an unauthenticated request cannot even arrive.

FAIL CLOSED ON THE CREDENTIAL, AND FAIL AT BOOT. A door that starts without a token and serves an
open endpoint is the worst outcome available: it looks healthy, it accepts everything, and the
first evidence is a stranger's row on the operator's board. `require_token()` raises at import of
the app rather than at the first request, so the failure is a service that will not start and is
visible in `systemctl --user --failed`, not a service that is green and wrong.

THE OFF-HOST QUESTION IS DELIBERATELY LEFT OPEN. The vision element this service exists for asks
for an endpoint that Make, Zapier or a cloud n8n can reach, and a loopback bind satisfies only
same-host producers such as this host's own `brain-n8n.service`. Exposing it is a separate
operator decision with an existing precedent on this host (`tailscale serve`, or the move to
a remote agent server), and it is escalated rather than taken. `INTAKE_HOST` is the one line that
would change, and the registry row must change first.
"""

from __future__ import annotations

import os

# --------------------------------------------------------------------------- the four values

#: The socket. Loopback is the sanctioned default and the only one this sprint binds. `0.0.0.0`
#: would publish the door onto the operator's home wifi, where the tailnet is not the boundary --
#: the same judgement `web/bin/phone-reach.sh` already calls a defect for the console.
BIND_HOST = os.environ.get("INTAKE_HOST", "127.0.0.1")

#: 3106 is this service's REGISTERED port, allocated by the wave-1 admiral and recorded in
#: `your-brain`'s `tools/port-registry.md` BEFORE anything bound it. It was
#: allocated rather than computed because S1 and S3 were both about to derive the same
#: "lowest free port in 3100-3199" on the same day, and a port collision is invisible to a
#: file-ownership map. Change the registry first.
BIND_PORT = int(os.environ.get("INTAKE_PORT", "3106"))

#: The shared secret every producer sends. No default, ever: see the module docstring.
TOKEN_ENV = "INTAKE_TOKEN"

#: WHERE THE SECRET LIVES WHEN IT IS NOT IN THE ENVIRONMENT, AND THERE IS EXACTLY ONE OF THESE.
#:
#: The file holds the raw token and nothing else -- no `KEY=`, no quoting -- which is the shape
#: `engine/bin/scratch-db.sh::secret()` already reads the store's own credentials in, from
#: `~/.brain-postgres-secrets`. Mode 0600, native ext4, never `/mnt/c`, never in git.
#:
#: ONE FILE READ THE SAME WAY BY BOTH HALVES. `ingest/connectors/door.py` reads this exact path for
#: the pushing side. An earlier draft of the systemd unit instead pointed `EnvironmentFile=` at a
#: second file in `KEY=VALUE` form, which would have meant one secret living in two places in two
#: formats, and this repo has already written down what that costs: "two files each carrying their
#: own default is two answers, and the one that is wrong is the one nobody edited"
#: (`web/host.py`, on why the console's bind is asked for rather than copied).
TOKEN_FILE = os.environ.get(
    "INTAKE_TOKEN_FILE",
    os.path.join(os.path.expanduser("~"), ".intake-service-secrets", "intake-token"),
)

#: The largest `content` this door accepts, in bytes. Bytes do not go through this door;
#: attachments pass by reference. 256 KiB is generous for a message body and small enough that a
#: connector pushing a whole PDF as text is refused with a 413 that tells it what to do instead.
MAX_CONTENT_BYTES = int(os.environ.get("INTAKE_MAX_CONTENT_BYTES", str(256 * 1024)))


# --------------------------------------------------------------------------- what the app asks

def require_token() -> str:
    """The shared secret, or refuse to boot.

    Raises `RuntimeError` rather than returning `""` so that an unset credential can never be
    mistaken for an empty-but-valid one further down. An empty string compares equal to an empty
    header, which is exactly the shape that turns a missing secret into an open door.
    """
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        return token
    # The file, second and never first, so a deliberate export always wins over what is on disk.
    # An unreadable or empty file is treated as NO credential and never as an empty one: an empty
    # string compares equal to an empty header, which is the shape that turns a missing secret into
    # an open door.
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as fh:
            token = fh.read().strip()
    except OSError:
        token = ""
    if not token:
        raise RuntimeError(
            f"{TOKEN_ENV} is unset or empty and {TOKEN_FILE} is missing, unreadable or empty, "
            "so the intake door refuses to start. This is fail-closed on purpose: a door that "
            "boots without a credential serves an open endpoint that looks healthy. Write the raw "
            f"token to {TOKEN_FILE} with mode 0600, or export {TOKEN_ENV} in the shell that "
            "starts the service."
        )
    return token


def describe() -> str:
    """One line for the launcher to print, so the journal records where it actually bound.

    Never prints the token. It says whether one resolved, which is the fact worth logging: a
    reader of the journal needs to know the door is authenticated, not what the secret is.
    """
    try:
        require_token()
        cred = "credential resolved"
    except RuntimeError:
        cred = "NO CREDENTIAL (this process will refuse to serve)"
    return (
        f"intake door on http://{BIND_HOST}:{BIND_PORT} "
        f"(registered port 3106, loopback, local-attended) -- {cred}"
    )
