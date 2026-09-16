"""`POST /intake` against a real store. What a connector gets, and what lands.

A SCRATCH DATABASE, NEVER `brain`: `conftest.py` refuses otherwise and says why. Every row this
file writes carries this run's marker in `source_name` and the session teardown deletes exactly
those.

WHAT IS ASSERTED HERE THAT A UNIT TEST CANNOT REACH. That the transition accepts the arguments
this door passes it, that the dedup rules behave as `CONTRACT.md` describes them on a real table
with a real UNIQUE constraint, and that what the door wrote into `body` is what the filesystem
path's own parser reads back OUT of the row. `test_payload.py` proves the door builds the right
string; only this proves the string survived the column.
"""

from __future__ import annotations

import pytest

from swarm_engine import cli
from swarm_engine.transitions import OBJECTIVE_ORIGINS

pytestmark = pytest.mark.usefixtures("scratch_store")


# ------------------------------------------------------------------ the happy path

def test_a_valid_payload_lands_exactly_one_objective(post, row, run_source):
    r = post({"idempotency_key": f"{run_source}/k1", "origin": "human",
              "title": f"{run_source} one"})
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["deduped"] is False
    assert body["name"] == f"{run_source} one"
    assert body["objective"]

    landed = row(body["name"])
    assert landed is not None
    assert landed["state"] == "inbox"              # THE DOOR DOES NOT CLASSIFY
    assert landed["intake_format"] == "text"
    assert landed["source_name"] == f"{run_source}/default"
    assert landed["source_signature"] == f"{run_source}/k1"
    assert landed["bytes"] == len(landed["body"].encode("utf-8"))
    assert str(landed["id"]) == body["objective"]


def test_the_x_intake_key_header_works_exactly_as_the_bearer_one_does(client, token,
                                                                     run_source):
    r = client.post("/intake", json={
        "source": f"{run_source}/xkey", "timestamp": "2026-09-01T09:00:00Z",
        "author": "a@b.c", "content": "second header", "title": f"{run_source} xkey",
        "idempotency_key": f"{run_source}/xkey"},
        headers={"X-Intake-Key": token})
    assert r.status_code == 201, r.get_json()


def test_everything_that_arrived_is_in_the_row(post, row, run_source):
    r = post({"idempotency_key": f"{run_source}/k2", "title": f"{run_source} two",
              "origin": "machine",
              "attachments": [{"name": "a.pdf", "mime": "application/pdf",
                               "url": "https://example.invalid/a.pdf"}],
              "metadata": {"thread_id": "19a2f0c4b8e1", "labels": ["INBOX"]}})
    assert r.status_code == 201, r.get_json()
    landed = row(f"{run_source} two")
    for expected in ("2026-09-01T09:14:22Z", "someone@example.com",
                     "https://example.invalid/a.pdf", "19a2f0c4b8e1", "INBOX"):
        assert expected in landed["body"]
    assert landed["body"].endswith("Can we move Thursday to 3pm?")


def test_the_front_matter_round_trips_out_of_the_column(post, row, run_source):
    """What the door wrote is what the filesystem path's own parser reads back."""
    for origin in OBJECTIVE_ORIGINS:
        name = f"{run_source} rt {origin}"
        assert post({"idempotency_key": f"{run_source}/rt/{origin}", "title": name,
                     "origin": origin}).status_code == 201
        assert cli._declared_origin(row(name)["body"]) == origin


# ------------------------------------------------------------------ dedup

def test_the_same_idempotency_key_twice_lands_one_objective(post, scratch_store, run_source):
    key = f"{run_source}/dedup"
    first = post({"idempotency_key": key, "title": f"{run_source} dedup"})
    assert first.status_code == 201, first.get_json()

    # A SECOND PUSH THE WAY A FIVE-MINUTE SCHEDULE MAKES IT: same key, different title, so the
    # name check cannot be what holds. If the pair check were dead this would land a second row.
    second = post({"idempotency_key": key, "title": f"{run_source} dedup, retitled"})
    assert second.status_code == 200, second.get_json()
    assert second.get_json()["deduped"] is True
    assert second.get_json()["reason"] == "already seen"
    assert second.get_json()["objective"] == first.get_json()["objective"]
    assert second.get_json()["name"] == f"{run_source} dedup"      # the row that already exists

    with scratch_store.read() as s:
        rows = s.query("SELECT id FROM brain.objective WHERE source_signature = %s", (key,))
    assert len(rows) == 1


def test_a_byte_identical_repeat_dedups_with_no_key_at_all(post, scratch_store, run_source):
    """The derived signature is a content hash, so a repeat is the same signature. No clock."""
    source = f"{run_source}/nokey"
    first = post({"title": f"{run_source} nokey"}, source=source)
    assert first.status_code == 201, first.get_json()
    signature = first.get_json()["derived"]["source_signature"]
    assert signature.startswith("sha256:")

    second = post({"title": f"{run_source} nokey"}, source=source)
    assert second.status_code == 200, second.get_json()
    assert second.get_json()["deduped"] is True
    # WHICH RULE HELD, asserted rather than assumed, and this line is the whole point of the
    # scene. Both pushes carry the same title, so the NAME check would answer 200 as well -- and
    # that is exactly the mask `CONTRACT.md` describes on the filesystem path, where a signature
    # that changes every tick left only the name check holding while a comment claimed both did.
    # `already seen` is the PAIR rule; `name already present` is the net.
    assert second.get_json()["reason"] == "already seen"
    with scratch_store.read() as s:
        rows = s.query("SELECT id FROM brain.objective WHERE source_name = %s", (source,))
    assert len(rows) == 1


def test_a_derived_name_is_unique_per_source_and_key(post, row, run_source):
    a = post({"idempotency_key": f"{run_source}/n1"}, source=f"{run_source}/a")
    b = post({"idempotency_key": f"{run_source}/n2"}, source=f"{run_source}/b")
    assert a.status_code == 201 and b.status_code == 201
    assert a.get_json()["name"] != b.get_json()["name"]
    assert a.get_json()["derived"]["name"] == a.get_json()["name"]
    assert row(a.get_json()["name"]) is not None


def test_a_title_collision_is_the_safety_net_and_says_which_rule_held(post, run_source):
    """Two different items sharing a title collapse to one. The contract calls this the net."""
    title = f"{run_source} same title"
    assert post({"idempotency_key": f"{run_source}/t1", "title": title}).status_code == 201
    second = post({"idempotency_key": f"{run_source}/t2", "title": title,
                   "content": "genuinely different text"})
    assert second.status_code == 200
    assert second.get_json()["reason"] == "name already present"
    assert second.get_json()["objective"]


# ------------------------------------------------------------------ the origin column

def test_an_omitted_origin_is_null_and_not_human(post, row, run_source):
    """NULL is `nobody said`; 'human' is `somebody said a person put this here`."""
    name = f"{run_source} undeclared"
    assert post({"idempotency_key": f"{run_source}/o0", "title": name}).status_code == 201
    assert row(name)["origin"] is None


@pytest.mark.parametrize("origin", list(OBJECTIVE_ORIGINS))
def test_a_declared_origin_is_written(post, row, run_source, origin):
    name = f"{run_source} declared {origin}"
    assert post({"idempotency_key": f"{run_source}/o/{origin}", "title": name,
                 "origin": origin}).status_code == 201
    assert row(name)["origin"] == origin


def test_an_unrecognised_origin_is_refused(post, run_source):
    r = post({"idempotency_key": f"{run_source}/o9", "origin": "robot"})
    assert r.status_code == 400
    assert r.get_json()["field"] == "origin"
    assert r.get_json()["accepted"] == list(OBJECTIVE_ORIGINS)


# ------------------------------------------------------------------ refusals

def test_a_bad_token_is_401_naming_both_headers(client, run_source):
    r = client.post("/intake", json={"source": run_source, "timestamp": "t", "author": "a",
                                     "content": "c"},
                    headers={"Authorization": "Bearer wrong-token"})
    assert r.status_code == 401
    body = r.get_json()
    assert body["error"] == "unauthenticated"
    assert body["accepted_headers"] == ["Authorization: Bearer <token>",
                                        "X-Intake-Key: <token>"]
    assert "INTAKE_TOKEN" in body["hint"]


def test_no_token_at_all_is_401(client, run_source):
    r = client.post("/intake", json={"source": run_source, "timestamp": "t", "author": "a",
                                     "content": "c"})
    assert r.status_code == 401
    assert r.get_json()["error"] == "unauthenticated"


def test_a_wrong_header_name_is_401_and_the_body_says_which_two_work(client, token, run_source):
    """The seven-week scar: a working credential under a header the service does not read."""
    r = client.post("/intake", json={"source": run_source, "timestamp": "t", "author": "a",
                                     "content": "c"},
                    headers={"X-Api-Key": token})
    assert r.status_code == 401
    assert "X-Intake-Key: <token>" in r.get_json()["accepted_headers"]


@pytest.mark.parametrize("missing", ["source", "timestamp", "author", "content"])
def test_a_missing_required_field_is_400_naming_that_field(post, missing):
    r = post({missing: None})
    assert r.status_code == 400
    assert r.get_json()["field"] == missing
    assert missing in r.get_json()["error"]


def test_oversized_content_is_413(post, run_source):
    from intake_service import host
    r = post({"content": "x" * (host.MAX_CONTENT_BYTES + 1),
              "idempotency_key": f"{run_source}/big"})
    assert r.status_code == 413
    assert r.get_json()["field"] == "content"
    assert "attachments" in r.get_json()["error"]


def test_a_body_that_is_not_json_is_400(client, token):
    r = client.post("/intake", data="not json at all",
                    headers={"Authorization": f"Bearer {token}",
                             "Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.get_json()["field"] == "(body)"


def test_nothing_lands_when_the_payload_is_refused(post, scratch_store, run_source):
    """A refusal is a refusal: no partial row, no placeholder, nothing queued."""
    source = f"{run_source}/refused"
    assert post({"origin": "robot"}, source=source).status_code == 400
    with scratch_store.read() as s:
        assert s.query("SELECT id FROM brain.objective WHERE source_name = %s", (source,)) == []
