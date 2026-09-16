"""Health, the boot refusal, and every way the store can be the thing that failed.

WHY THE STORE IS INJECTED HERE AND NOWHERE ELSE. The three behaviours below matter most exactly
when Postgres is broken, and "break Postgres" is not a step a suite may take on a host the
operator is working on. So `create_app` takes the store module as an argument whose default is
the real one, and these scenes hand it a store that fails on purpose. `test_the_door.py` runs
against the real thing; this file runs against the failures the real thing will not perform to
order.
"""

from __future__ import annotations

import contextlib
import inspect

import psycopg2
import pytest

from intake_service import host
from intake_service.app import create_app
from swarm_engine import transitions as real_transitions


class Recorded:
    """A store that records the call and answers as the transition would. No database."""

    StoreConfigError = ValueError

    def __init__(self, result=None, raises=None):
        self.calls = []
        self._result = result or {"taken": True, "objective_id": 4242,
                                  "objective_name": "recorded", "origin": None}
        self._raises = raises

    def apply(self, verb, **kwargs):
        self.calls.append((verb, kwargs))
        if self._raises:
            raise self._raises
        return dict(self._result)

    @contextlib.contextmanager
    def read(self, *a, **k):
        raise self._raises or RuntimeError("this fake store has no read path")


class Down:
    """A store that is simply not there. Every door into it raises."""

    StoreConfigError = ValueError

    def __init__(self, exc):
        self.exc = exc

    def apply(self, verb, **kwargs):
        raise self.exc

    @contextlib.contextmanager
    def read(self, *a, **k):
        raise self.exc
        yield  # pragma: no cover


def payload(**over):
    raw = {"source": "suite:health", "timestamp": "2026-09-01T09:00:00Z",
           "author": "a@b.c", "content": "hello"}
    raw.update(over)
    return raw


def headers(token):
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------ the boot refusal

def _no_credential_anywhere(monkeypatch, tmp_path):
    """Clear BOTH sources.

    Clearing only the environment used to be enough and is not any more: `require_token()` now
    falls back to a file, which is what lets a systemd-started door and an n8n-scheduled connector
    resolve the same secret from one path. A test that cleared only the env var passed for the
    wrong reason the moment that file existed on a developer's machine -- and it did exactly that,
    caught by this suite going red rather than by anyone noticing.
    """
    monkeypatch.delenv(host.TOKEN_ENV, raising=False)
    monkeypatch.setattr(host, "TOKEN_FILE", str(tmp_path / "no-such-token"))


def test_the_app_refuses_to_construct_without_a_credential(monkeypatch, tmp_path):
    """A service that will not start, not a service that is green and open."""
    _no_credential_anywhere(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError) as e:
        create_app(store=Recorded())
    assert host.TOKEN_ENV in str(e.value)


def test_an_empty_credential_is_not_a_credential(monkeypatch, tmp_path):
    """An empty string compares equal to an empty header. That is the open-door shape."""
    _no_credential_anywhere(monkeypatch, tmp_path)
    monkeypatch.setenv(host.TOKEN_ENV, "   ")
    with pytest.raises(RuntimeError):
        create_app(store=Recorded())


def test_the_token_file_is_a_credential_source(monkeypatch, tmp_path):
    """The positive control for the fallback.

    Without it, a `TOKEN_FILE` that could never be read would pass every refusal test above and
    look like a working fallback while a scheduled connector silently had no credential.
    """
    monkeypatch.delenv(host.TOKEN_ENV, raising=False)
    f = tmp_path / "intake-token"
    f.write_text("a-token-from-a-file\n", encoding="utf-8")
    monkeypatch.setattr(host, "TOKEN_FILE", str(f))
    assert host.require_token() == "a-token-from-a-file"
    create_app(store=Recorded())                       # constructs, which is the whole point


def test_the_environment_wins_over_the_file(monkeypatch, tmp_path):
    """A deliberate export beats whatever is on disk. Order matters and is asserted, not assumed."""
    f = tmp_path / "intake-token"
    f.write_text("from-the-file", encoding="utf-8")
    monkeypatch.setattr(host, "TOKEN_FILE", str(f))
    monkeypatch.setenv(host.TOKEN_ENV, "from-the-environment")
    assert host.require_token() == "from-the-environment"


def test_a_blank_token_file_is_no_credential_and_not_an_empty_one(monkeypatch, tmp_path):
    """A file of whitespace must refuse. An empty string equals an empty header: the open door."""
    monkeypatch.delenv(host.TOKEN_ENV, raising=False)
    f = tmp_path / "intake-token"
    f.write_text("   \n", encoding="utf-8")
    monkeypatch.setattr(host, "TOKEN_FILE", str(f))
    with pytest.raises(RuntimeError):
        host.require_token()


# ------------------------------------------------------------------ health

def test_health_is_200_against_a_live_store_and_names_what_it_proved(door, scratch_store):
    r = door.test_client().get("/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["service"] == "brain-intake"
    assert body["bind"] == f"http://{host.BIND_HOST}:{host.BIND_PORT}"
    assert body["store"]["read"] is True
    assert body["store"]["database"]
    assert body["credential"] == {"env": host.TOKEN_ENV, "resolved": True}


def test_health_needs_no_token(door):
    assert door.test_client().get("/health").status_code == 200


def test_health_never_serves_the_credential(door, token):
    assert token not in door.test_client().get("/health").get_data(as_text=True)


def test_a_down_store_is_500_and_never_a_cheerful_degraded_200(token):
    app = create_app(store=Down(psycopg2.OperationalError("could not connect to server")))
    r = app.test_client().get("/health")
    assert r.status_code == 500
    assert r.get_json()["store"]["read"] is False
    assert "could not connect" in r.get_json()["store"]["reason"]


# ------------------------------------------------------------------ the store failing under a POST

@pytest.mark.parametrize("exc", [psycopg2.OperationalError("server closed the connection"),
                                 ValueError("no credential resolved")])
def test_a_store_that_refuses_is_503_and_holds_nothing(token, exc):
    app = create_app(store=Down(exc))
    r = app.test_client().post("/intake", json=payload(), headers=headers(token))
    assert r.status_code == 503
    assert r.get_json()["retry"] is True
    assert "nothing is held here" in r.get_json()["error"]


def test_a_verb_refusal_is_400_and_carries_the_verbs_own_words(token):
    app = create_app(store=Recorded(raises=real_transitions.VerbError("the engine said no")))
    r = app.test_client().post("/intake", json=payload(), headers=headers(token))
    assert r.status_code == 400
    assert "the engine said no" in r.get_json()["error"]


# ------------------------------------------------------------------ what the door passes the verb

def test_the_door_passes_only_arguments_the_transition_accepts(token):
    """`intake` accepts nothing else. A future field added here would be refused by the engine."""
    store = Recorded()
    app = create_app(store=store)
    r = app.test_client().post("/intake", json=payload(origin="human", title="t",
                                                       idempotency_key="k",
                                                       metadata={"a": 1}),
                               headers=headers(token))
    assert r.status_code == 201, r.get_json()
    verb, kwargs = store.calls[0]
    assert verb == "intake"
    accepted = set(inspect.signature(real_transitions.intake).parameters) - {"ctx"}
    assert set(kwargs) <= accepted, set(kwargs) - accepted
    assert kwargs["intake_format"] == "text"
    assert kwargs["source_name"] == "suite:health"
    assert kwargs["source_signature"] == "k"
    assert kwargs["name"] == "t"


def test_the_accepted_origins_come_from_the_engine_and_are_not_spelled_in_the_door(token):
    """Hand the door a transitions module that knows a third origin and it accepts it."""
    class Wider:
        OBJECTIVE_ORIGINS = ("human", "machine", "seagull")
        VerbError = real_transitions.VerbError

    store = Recorded()
    app = create_app(store=store, transitions=Wider())
    r = app.test_client().post("/intake", json=payload(origin="seagull"),
                               headers=headers(token))
    assert r.status_code == 201, r.get_json()
    assert store.calls[0][1]["origin"] == "seagull"

    # And the REAL door, on the real tuple, refuses the same word.
    real = create_app(store=Recorded())
    assert real.test_client().post("/intake", json=payload(origin="seagull"),
                                   headers=headers(token)).status_code == 400


def test_the_door_passes_who_and_when_through_to_the_transition(token):
    """`author` and `occurred_at` reach the verb, so migration 53's columns are not dead weight.

    The transition writes each only when its column exists (`schema.has_column`), so on a store at
    ledger 52 these are silently skipped and the door behaves exactly as before. That tolerance is
    the reason this can be passed unconditionally -- and it is also why the mistake this test
    guards is invisible without it: a door that quietly stopped passing them would go on returning
    201 forever, and the defect would surface only as an inbox that cannot be sorted by time or
    attributed to a sender.
    """
    store = Recorded()
    app = create_app(store=store)
    r = app.test_client().post(
        "/intake",
        json=payload(origin="human", title="t", idempotency_key="k"),
        headers=headers(token))
    assert r.status_code == 201, r.get_json()
    _verb, kwargs = store.calls[0]
    assert kwargs["author"] == payload()["author"], kwargs.get("author")
    assert kwargs["occurred_at"] == payload()["timestamp"], kwargs.get("occurred_at")


def test_when_is_the_message_clock_and_never_the_sweep_clock(token):
    """`occurred_at` is the payload's own timestamp, not now().

    A door that substituted its own clock would make every late-swept message look fresh, which is
    exactly the sort defect the column exists to fix, and every row would still look plausible.
    """
    store = Recorded()
    app = create_app(store=store)
    old = "2021-03-04T05:06:07Z"
    r = app.test_client().post("/intake",
                               json=payload(timestamp=old, idempotency_key="k2", title="t2"),
                               headers=headers(token))
    assert r.status_code == 201, r.get_json()
    _verb, kwargs = store.calls[0]
    assert kwargs["occurred_at"] == old, kwargs.get("occurred_at")
