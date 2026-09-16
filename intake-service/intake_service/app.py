"""THE INTAKE DOOR: two routes, one verb, no classification.

`POST /intake` lands ONE unclassified objective in `state='inbox'` and `GET /health` says whether
this process can reach the store. That is the whole service. `CONTRACT.md` is the interface a
connector author codes against and this file implements it; where the two disagree, the contract
is the specification and this file is the defect.

------------------------------------------------------------------------------------------------
WHY THIS IS A SERVICE AND NOT A ROUTE ON THE CONSOLE
------------------------------------------------------------------------------------------------
Decided by the operator, 2026-09-01 (`d-intake-separate-service`). The console has ONE write door
and a test asserts it; a second one would spend that property. This also survives the move to a
VPS, where the console is not what a cloud connector can reach.

------------------------------------------------------------------------------------------------
THE ONLY WAY THIS PROCESS CAN WRITE
------------------------------------------------------------------------------------------------
`store.apply("intake", ...)`. There is no `execute`, no `insert`, no cursor and no connection
reachable from here: `store.read()` hands out a session whose transaction POSTGRES ITSELF holds
READ ONLY, so the `/health` read cannot become a write however this file is later edited. The
transition is `engine/swarm_engine/transitions.py`'s and it is called, never reimplemented.

**IT LANDS `state='inbox'` AND NOTHING HERE CLASSIFIES.** Not the origin, not the urgency, not
the project. A wrong classification is the same defect class as a fabricated transcription
because it will be believed later, so the machine lands one row and a human sorts it.

------------------------------------------------------------------------------------------------
WHAT FAILS AT BOOT, ON PURPOSE
------------------------------------------------------------------------------------------------
`host.require_token()` is called in `create_app`, not in the request handler. An unset
`INTAKE_TOKEN` therefore produces a service that WILL NOT START and shows up in
`systemctl --user --failed`, rather than a service that is green and open. A door that boots
without a credential is the worst outcome available: it looks healthy, it accepts everything, and
the first evidence is a stranger's row on the operator's board.
"""

from __future__ import annotations

import hmac

from flask import Flask, jsonify, request

from . import host
from . import capture_gate
from .payload import PayloadError, parse

import psycopg2                                                          # noqa: E402
import store as _store                                                   # noqa: E402
from swarm_engine import transitions as _transitions                     # noqa: E402

#: Registering the verb is an IMPORT SIDE EFFECT of the line above, and the import is therefore
#: not unused. The MCP server shipped without its equivalent once and every write tool failed
#: while every read worked, which is the quietest possible version of this mistake.
VERB = "intake"

#: The body `CONTRACT.md` promises on a rejected credential, verbatim. Both headers are named in
#: it because the failure this door was built after was a working credential declared dead for
#: seven weeks over a header name, and a 401 that does not say what it accepts cannot end that.
UNAUTHENTICATED = {
    "error": "unauthenticated",
    "accepted_headers": ["Authorization: Bearer <token>", "X-Intake-Key: <token>"],
    "hint": "the token is the value of INTAKE_TOKEN on the host running this service",
}


def _presented(req) -> list:
    """Every token this request offers, from either accepted header.

    Both, never one. See `CONTRACT.md`: accepting both costs four lines and removes an entire
    failure mode, and the mode it removes has already cost this estate seven weeks.
    """
    out = []
    authorization = (req.headers.get("Authorization") or "").strip()
    if authorization[:7].lower() == "bearer ":
        out.append(authorization[7:].strip())
    key = (req.headers.get("X-Intake-Key") or "").strip()
    if key:
        out.append(key)
    return [t for t in out if t]


def _authenticated(req, token: str) -> bool:
    """`hmac.compare_digest`, never `==`.

    `==` on a secret returns as soon as two bytes differ, so the time it takes is a measurement of
    how much of the token the caller already has. This door is loopback today and the comparison
    is four characters either way; the version of this file that gets exposed is not going to be
    the one that also remembers to change this line.
    """
    expected = token.encode("utf-8")
    return any(hmac.compare_digest(candidate.encode("utf-8"), expected)
               for candidate in _presented(req))


def create_app(store=None, transitions=None, capture=None) -> Flask:
    """Build the app, or refuse to.

    `store` and `transitions` are injection seams for the suite and nothing else: the defaults
    are the real modules and no caller in production passes either. They exist because the two
    failures worth proving hardest -- a store that is DOWN, and an origin the ENGINE has not
    heard of -- are otherwise only reachable by breaking a database, and a test that cannot be
    run is a test nobody runs.

    `capture` is DURABLE CAPTURE IN FRONT OF THE ACKNOWLEDGEMENT, and it defaults to OFF. With
    `None` this door behaves byte for byte as it did before -- same writes, same answers, same
    status codes -- because turning capture on for a service a connector already trusts changes
    what a 201 means, and that is an operational decision with its own authorisation, not a
    side effect of a code packet. See `capture_gate.py`. When a composition root passes a gate,
    evidence is durable BEFORE `store.apply` runs, and a gate failure means nothing is written
    and nothing is acknowledged.
    """
    store = store or _store
    transitions = transitions or _transitions

    # AT CONSTRUCTION. Not at the first request, not lazily. See the module docstring.
    token = host.require_token()

    app = Flask(__name__)

    # A cap on the RAW REQUEST, well above the content cap, so a 500 MB POST is refused by the
    # framework before it is decoded rather than after it is in memory. It is not the contract's
    # limit and must not be read as one: `MAX_CONTENT_BYTES` applies to the `content` FIELD, and
    # the JSON around it, plus escaping, plus metadata, is legitimately larger than the field.
    app.config["MAX_CONTENT_LENGTH"] = host.MAX_CONTENT_BYTES * 4 + 256 * 1024

    @app.errorhandler(413)
    def _too_large(_exc):
        # Flask's own 413 is an HTML page. A connector author gets the same JSON shape here as
        # from the field-level check, because a body it cannot parse is a body it cannot act on.
        return jsonify({
            "error": f"the request body is larger than this door accepts "
                     f"({app.config['MAX_CONTENT_LENGTH']} bytes of JSON). `content` itself is "
                     f"capped at {host.MAX_CONTENT_BYTES} bytes: truncate it and pass a "
                     f"reference in `attachments`. Bytes do not go through this door.",
            "field": "(body)",
            "limit_bytes": host.MAX_CONTENT_BYTES,
        }), 413

    @app.post("/intake")
    def intake():
        if not _authenticated(request, token):
            # BEFORE THE BODY IS PARSED. An unauthenticated caller learns nothing about which of
            # its fields this door would have complained about.
            return jsonify(UNAUTHENTICATED), 401

        # `force=True` because Make, Zapier and more than one n8n node send a JSON body under a
        # content type they chose themselves, and a door that refused those would be refusing
        # correct payloads over a header. `silent=True` so a decode failure is this file's own
        # sentence rather than a Werkzeug HTML page.
        raw = request.get_json(force=True, silent=True)
        if raw is None:
            return jsonify({
                "error": "the request body did not decode as JSON. Send the object described in "
                         "CONTRACT.md.",
                "field": "(body)",
            }), 400

        try:
            payload = parse(raw,
                            origins=transitions.OBJECTIVE_ORIGINS,
                            max_content_bytes=host.MAX_CONTENT_BYTES)
        except PayloadError as exc:
            return jsonify(exc.body()), exc.status

        name, signature = payload.name(), payload.signature()
        derived = {}
        if payload.signature_was_derived():
            # SAID OUT LOUD, because a connector author who never sees this line will not learn
            # that their retries are deduping on the content rather than on a key they control.
            derived["source_signature"] = signature
        if payload.name_was_derived():
            derived["name"] = name

        # DURABLE CAPTURE FIRST, WHEN IT IS CONFIGURED AT ALL. Evidence before the work item:
        # once `store.apply` lands a row this door has taken the item in, and if the original
        # payload was never preserved there is nothing left to check that row against. With no
        # gate injected this block is a single `if` that does not fire.
        captured = None
        if capture is not None:
            try:
                captured = capture.capture(payload, raw)
            except capture_gate.CaptureRefused as exc:
                # Permanent for these bytes. Same shape as the payload rules above: a 400 tells
                # the connector that retrying this body unchanged will fail identically for ever.
                return jsonify({"error": str(exc), "field": "(capture)"}), 400
            except capture_gate.CaptureUnavailable as exc:
                # 503 AND NOTHING IS WRITTEN, for the same reason the store-down branch below
                # gives: the connector is the queue and still holds the item.
                return jsonify({"error": str(exc), "retry": True}), 503

        try:
            result = store.apply(
                VERB,
                name=name,
                body=payload.body(),
                source_name=payload.source,
                source_signature=signature,
                bytes_=payload.body_bytes(),
                # `text` always. `INTAKE_FORMATS` is closed at ('text','voice') and a voice
                # objective must carry a media pointer and a transcription status, which this
                # door does not accept and must not invent.
                intake_format="text",
                origin=payload.origin,
                # WHO AND WHEN, PASSED THROUGH RATHER THAN LEFT IN THE FRONT MATTER ALONE.
                #
                # The transition writes each of these only when the column exists
                # (`schema.has_column`), so on the live store today -- ledger 52, no such columns
                # -- both are silently skipped and this door behaves exactly as it did before.
                # When migration 53 is applied they start landing in columns, with no change here
                # and no redeploy.
                #
                # THEY ARE PASSED EVEN THOUGH THE FRONT MATTER ALREADY CARRIES THEM, and that is
                # not redundancy for its own sake. Front matter is the wire record, faithful and
                # unindexed; a column is the queryable projection, and "who is waiting on me" and
                # "newest first" are the two questions anyone actually asks of an inbox. Neither
                # can be answered by grepping a body.
                #
                # The alternative was to apply 53 and leave nothing writing the columns, which
                # migration 53's own comments call dead weight. This is the line that stops it
                # being dead weight.
                author=payload.author,
                occurred_at=payload.timestamp,
            )
        except transitions.VerbError as exc:
            # The transition refusing a payload is a statement about the payload, so it is a 400
            # and it carries the verb's own words. Every rule it enforces that this door can
            # check is already checked above; this is the arm that stays correct when the engine
            # grows a rule the door has not learned yet.
            return jsonify({"error": str(exc), "field": "(store)"}), 400
        except (store.StoreConfigError, psycopg2.Error) as exc:
            # 503 AND NOTHING IS QUEUED HERE. A silent local buffer is how an intake system loses
            # work it has already told a producer it took. The connector is the queue: it holds
            # the item and retries. A unique-violation race on `name` lands here too, and retry
            # is the correct response to it as well: the second attempt reads the row the first
            # one landed and answers 200 deduped.
            return jsonify({
                "error": f"the store refused or was unreachable, so nothing was written and "
                         f"nothing is held here: {exc.__class__.__name__}: {exc}",
                "retry": True,
            }), 503

        if result.get("taken"):
            answer = _answer(str(result["objective_id"]), result["objective_name"],
                             False, derived)
            if captured is not None:
                answer.update(captured.as_answer_fields())
            return jsonify(answer), 201

        # ALREADY SEEN, AND THE ID IS LOOKED UP RATHER THAN GUESSED. The transition's two dedup
        # branches return no `objective_id` at all -- the first returns the existing row's NAME,
        # the second returns only the name it was given -- and `CONTRACT.md` promises the id on a
        # 200. So it is read back through `store.read()`, which is the public surface and cannot
        # write. A read that fails here is a store failure like any other: 503, retry.
        landed = result.get("objective_name") or name
        try:
            with store.read() as session:
                row = session.one("SELECT id FROM brain.objective WHERE name = %s", (landed,))
        except (store.StoreConfigError, psycopg2.Error) as exc:
            return jsonify({
                "error": f"this item was already taken in, and the store then became unreachable "
                         f"before its id could be read back: {exc.__class__.__name__}: {exc}",
                "retry": True,
            }), 503
        body = _answer(str(row["id"]) if row else None, landed, True, derived)
        if captured is not None:
            body.update(captured.as_answer_fields())
        # WHICH of the two dedup rules held. Additive to the contract's three keys, and it is the
        # difference between "your key worked" and "a different item happened to share this
        # title", which is a distinction the contract itself calls a safety net rather than the
        # design.
        body["reason"] = result.get("reason") or "already seen"
        return jsonify(body), 200

    @app.get("/health")
    def health():
        """Reachability, proved by a real read. No token: nothing else is unauthenticated.

        A DOWN STORE IS A 500 AND NEVER A CHEERFUL DEGRADED 200. That is the console's own rule
        and the reason its row in the port registry says so: an absent service is obviously
        absent, and a 200 that means "up but cannot write anything" is a lie a monitor believes.
        """
        out = {
            "service": "brain-intake",
            "bind": f"http://{host.BIND_HOST}:{host.BIND_PORT}",
            "max_content_bytes": host.MAX_CONTENT_BYTES,
        }
        # WHETHER a credential resolved, NEVER the credential. A door that is up and
        # unauthenticated is worse than a door that is down, so this is a fact worth serving; the
        # secret is not.
        try:
            host.require_token()
            out["credential"] = {"env": host.TOKEN_ENV, "resolved": True}
        except RuntimeError as exc:
            out["credential"] = {"env": host.TOKEN_ENV, "resolved": False, "reason": str(exc)}

        try:
            with store.read() as session:
                out["store"] = {
                    "read": True,
                    "database": session.scalar("SELECT current_database()"),
                    "schema_version": session.scalar(
                        "SELECT max(version) FROM brain.schema_migration"),
                }
        except Exception as exc:                                          # noqa: BLE001
            out["store"] = {"read": False,
                            "reason": f"{exc.__class__.__name__}: {exc}"}
            return jsonify(out), 500

        return jsonify(out), 200

    return app


def _answer(objective_id, name, deduped: bool, derived: dict) -> dict:
    """The three keys `CONTRACT.md` promises, plus what this door derived on the caller's behalf."""
    out = {"objective": objective_id, "name": name, "deduped": deduped}
    if derived:
        out["derived"] = derived
    return out


#: What `flask run` imports. `bin/intake-service` names it as `intake_service.app:app`, and this
#: is the line that raises when `INTAKE_TOKEN` is unset.
app = create_app()
