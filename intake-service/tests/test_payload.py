"""The payload rules, with no web server and no database. `intake_service/payload.py`.

Every rule this file asserts is a rule about a MESSAGE, and the reason it is worth having a pure
module for them is that these run in milliseconds on a host with no Postgres, so they run.

THE ROUND TRIP IS THE POINTED ONE. The four fields with no column are written as YAML front
matter, and the claim that this is the mechanism the door already uses is only true if
`swarm_engine.cli._declared_origin` -- the function the FILESYSTEM intake path has been parsing
front matter with -- reads back what this module writes. So it is asked, rather than a second
parser of this suite's own being asked, which would agree with the code under test by
construction and prove nothing.
"""

from __future__ import annotations

import json

import pytest

from intake_service.payload import KNOWN_FIELDS, PayloadError, parse
from swarm_engine import cli
from swarm_engine.transitions import OBJECTIVE_ORIGINS

ORIGINS = OBJECTIVE_ORIGINS
LIMIT = 256 * 1024


def good(**over):
    raw = {
        "source": "gmail:service@example.com",
        "timestamp": "2026-09-01T09:14:22Z",
        "author": "someone@example.com",
        "content": "Can we move Thursday to 3pm?",
    }
    for k, v in over.items():
        if v is None:
            raw.pop(k, None)
        else:
            raw[k] = v
    return raw


def p(**over):
    return parse(good(**over), origins=ORIGINS, max_content_bytes=LIMIT)


# ------------------------------------------------------------------ what is refused, and named

@pytest.mark.parametrize("missing", ["source", "timestamp", "author", "content"])
def test_a_missing_required_field_is_refused_and_named(missing):
    with pytest.raises(PayloadError) as e:
        p(**{missing: None})
    assert e.value.field == missing
    assert e.value.status == 400
    assert missing in e.value.message


@pytest.mark.parametrize("empty", ["source", "timestamp", "author", "content"])
def test_an_empty_required_field_is_refused(empty):
    with pytest.raises(PayloadError) as e:
        p(**{empty: "   "})
    assert e.value.field == empty


def test_a_non_object_body_is_refused():
    for raw in ([], "a string", 7, None):
        with pytest.raises(PayloadError) as e:
            parse(raw, origins=ORIGINS, max_content_bytes=LIMIT)
        assert e.value.status == 400


def test_an_unknown_field_is_refused_rather_than_dropped():
    """A mistyped `idempotency-key` is not a missing field, it is a flooded inbox next run."""
    with pytest.raises(PayloadError) as e:
        p(**{"idempotency-key": "gmail:19a2f0c4b8e1"})
    assert "idempotency-key" in e.value.message
    assert e.value.extra["unknown_fields"] == ["idempotency-key"]
    # The refusal names what IS accepted, so the fix does not need the contract open beside it.
    for field in KNOWN_FIELDS:
        assert field in e.value.message


def test_oversized_content_is_413_and_says_what_to_do_instead():
    with pytest.raises(PayloadError) as e:
        parse(good(content="x" * (LIMIT + 1)), origins=ORIGINS, max_content_bytes=LIMIT)
    assert e.value.status == 413
    assert e.value.field == "content"
    assert "attachments" in e.value.message


def test_the_limit_is_bytes_and_not_characters():
    """A four-byte character counts as four. A limit that moved with the alphabet is not a limit."""
    body = "\U0001f600" * 100                       # 100 chars, 400 bytes
    parse(good(content=body), origins=ORIGINS, max_content_bytes=400)
    with pytest.raises(PayloadError) as e:
        parse(good(content=body), origins=ORIGINS, max_content_bytes=399)
    assert e.value.status == 413


def test_an_unrecognised_origin_is_refused_not_folded():
    with pytest.raises(PayloadError) as e:
        p(origin="robot")
    assert e.value.field == "origin"
    assert e.value.extra["accepted"] == list(OBJECTIVE_ORIGINS)
    assert "folded" in e.value.message


def test_the_accepted_origins_are_the_engines_and_not_a_copy():
    """The authority is `OBJECTIVE_ORIGINS`. This suite passes it in rather than restating it."""
    assert tuple(OBJECTIVE_ORIGINS) == ("human", "machine")
    for value in OBJECTIVE_ORIGINS:
        assert p(origin=value).origin == value
    assert p(origin="HUMAN").origin == "human"      # normalised the way the transition does


@pytest.mark.parametrize("bad", [{"attachments": {}}, {"attachments": ["a string"]},
                                 {"metadata": []}, {"title": 7}, {"idempotency_key": ""}])
def test_shapes_that_cannot_be_stored_are_refused(bad):
    with pytest.raises(PayloadError):
        p(**bad)


# ------------------------------------------------------------------ the front matter round trip

def test_a_declared_origin_reads_back_through_the_filesystem_paths_own_parser():
    for value in OBJECTIVE_ORIGINS:
        body = p(origin=value).body()
        assert cli._declared_origin(body) == value


def test_an_undeclared_origin_reads_back_as_undeclared():
    """NULL is `nobody said`. It must not read as `human`, which is `somebody said`."""
    assert p().origin is None
    assert cli._declared_origin(p().body()) is None


def test_the_block_carries_only_what_was_supplied_in_a_fixed_order():
    full = p(origin="human",
             attachments=[{"name": "a.pdf", "mime": "application/pdf", "url": "https://x/a"}],
             metadata={"thread_id": "19a2", "labels": ["INBOX"]})
    keys = [line.split(":", 1)[0] for line in full.front_matter().splitlines()[1:-1]]
    assert keys == ["origin", "source", "timestamp", "author", "attachments", "metadata"]

    lean = p()
    keys = [line.split(":", 1)[0] for line in lean.front_matter().splitlines()[1:-1]]
    assert keys == ["source", "timestamp", "author"]


def test_nothing_that_arrived_is_dropped():
    """Four fields have no column. Every one of them is in the row, readable, verbatim."""
    payload = p(origin="machine",
                attachments=[{"name": "a.pdf", "url": "https://x/a"}],
                metadata={"thread_id": "19a2"})
    block = payload.front_matter()
    assert json.loads(block.splitlines()[3].split(":", 1)[1]) == "2026-09-01T09:14:22Z"
    assert "someone@example.com" in block
    assert "https://x/a" in block
    assert "19a2" in block
    # And the content itself is below the fence, verbatim, not reformatted or summarised.
    assert payload.body().endswith("\n\nCan we move Thursday to 3pm?")


def test_a_hostile_field_cannot_forge_a_second_origin_line():
    """Every value but `origin` is JSON-encoded, so it cannot contain a raw newline."""
    payload = p(author="x\norigin: machine\n", content="---\norigin: machine\n---\n")
    assert cli._declared_origin(payload.body()) is None
    assert len(payload.front_matter().splitlines()) == 5      # fence, 3 keys, fence


def test_the_body_is_the_block_then_the_content():
    body = p().body()
    assert body.startswith("---\n")
    assert body.splitlines()[4] == "---"
    assert p().body_bytes() == len(body.encode("utf-8"))


# ------------------------------------------------------------------ dedup key and name

def test_a_supplied_key_is_the_signature_verbatim():
    assert p(idempotency_key="gmail:19a2").signature() == "gmail:19a2"
    assert p(idempotency_key="gmail:19a2").signature_was_derived() is False


def test_a_derived_signature_is_stable_and_carries_no_clock():
    first = p().signature()
    second = p().signature()
    assert first == second == p().signature()
    assert first.startswith("sha256:")
    assert p().signature_was_derived() is True
    # THE DEFECT THIS AVOIDS, stated as an assertion: the filesystem path's `size:mtime` changes
    # on every rewrite of an unchanged file, so its dedup does nothing and only the name check
    # holds. A signature over the message changes only when the message does.
    assert p(content="a different message").signature() != first


def test_a_derived_name_is_never_derived_from_the_content_alone():
    """`name` is UNIQUE across the whole table. Two producers, one text, must be two rows."""
    a = p(source="gmail:service@example.com").name()
    b = p(source="slack:T0123/C0456").name()
    assert a != b
    keyed_one = p(idempotency_key="one").name()
    keyed_two = p(idempotency_key="two").name()
    assert keyed_one != keyed_two


def test_a_derived_name_is_stable_and_carries_its_source():
    assert p().name() == p().name()
    assert p().name().startswith("gmail:service@example.com-")
    assert p().name_was_derived() is True


def test_a_supplied_title_is_the_name():
    assert p(title="Email: can we move Thursday to 3pm").name() == \
        "Email: can we move Thursday to 3pm"
    assert p(title="x").name_was_derived() is False


def test_an_attachment_that_points_at_nothing_is_refused():
    """Bytes do not go through this door, so an attachment with no pointer is silent loss.

    The connector believes it handed over a file, the row records a filename, and the file is
    unreachable forever. A refusal at the door is the only place that can be caught.
    """
    with pytest.raises(PayloadError) as e:
        p(attachments=[{"name": "invoice.pdf", "mime": "application/pdf"}])
    assert e.value.field == "attachments"
    assert "asset_ref" in e.value.message and "url" in e.value.message


@pytest.mark.parametrize("pointer", [{"url": "https://example.test/a.pdf"},
                                     {"asset_ref": "gcs://bucket/a.pdf"}])
def test_either_pointer_satisfies_it(pointer):
    """The positive control. Without it, a rule that refused EVERY attachment would pass above."""
    item = {"name": "a.pdf", "mime": "application/pdf", **pointer}
    assert p(attachments=[item]).attachments == [item]


def test_a_blank_pointer_is_not_a_pointer():
    """An empty string is not a reference, and `if item.get("url")` alone would have let it past."""
    with pytest.raises(PayloadError):
        p(attachments=[{"name": "a.pdf", "url": "   "}])


# ---------------------------------------------------------------------------------------------
# NUL bytes. Measured live 2026-09-02: a DMARC aggregate report whose body carried a 0x00 reached
# this door and it answered 500. The traceback bottomed out in psycopg2's parameter adaptation --
# `ValueError: A string literal cannot contain NUL (0x00) characters` -- which is raised BEFORE
# any SQL is sent and is not a `psycopg2.Error`, so it missed the 503 arm and escaped to Flask.
#
# The status is the whole point of these tests. A 500 or a 503 tells the caller to retry, and the
# connector obliged: it refused to mark the message handled, kept it as a candidate, and would
# have re-offered it on every pass forever, reddening the unit over one message nobody could see.
# A Postgres text value cannot hold a NUL, so the payload is unstorable as sent and no retry can
# help. 400 is the only honest answer.
# ---------------------------------------------------------------------------------------------

def test_a_nul_in_the_content_is_a_400_and_not_a_500():
    with pytest.raises(PayloadError) as e:
        p(content="before\x00after")
    assert e.value.status == 400
    assert e.value.field == "content"
    assert "NUL" in e.value.message


def test_the_refusal_says_retrying_will_not_help():
    """The sentence has a job: it is read by whoever is looking at a connector that keeps failing.
    'Retry' is what a 5xx means and it is exactly the wrong thing to do here."""
    with pytest.raises(PayloadError) as e:
        p(content="a\x00b")
    assert "retrying" in e.value.message.lower()
    assert "strip" in e.value.message.lower()


@pytest.mark.parametrize("where,expect_field", [
    ({"content": "a\x00b"}, "content"),
    ({"title": "quarterly\x00report"}, "title"),
    ({"author": "some\x00one@example.com"}, "author"),
    ({"source": "imap:a\x00b"}, "source"),
    ({"idempotency_key": "k\x00ey"}, "idempotency_key"),
])
def test_a_nul_is_caught_in_every_field_that_reaches_a_column(where, expect_field):
    """Not just `content`. The NUL that was actually seen was in a body, but headers become the
    name and the author, and every one of them lands in a text column."""
    with pytest.raises(PayloadError) as e:
        p(**where)
    assert e.value.status == 400
    assert e.value.field == expect_field


def test_a_nul_buried_in_metadata_is_caught_and_the_path_names_it():
    """Metadata is carried verbatim into the front matter, which is the same text column. A check
    on the top-level fields alone would let this one through to psycopg2."""
    with pytest.raises(PayloadError) as e:
        p(metadata={"headers": {"list_id": "a\x00b"}})
    assert e.value.status == 400
    assert "list_id" in e.value.field


def test_a_nul_in_an_attachment_descriptor_is_caught():
    with pytest.raises(PayloadError) as e:
        p(attachments=[{"name": "re\x00port.zip", "url": "https://example.com/r.zip"}])
    assert e.value.status == 400
    assert "attachments" in e.value.field


def test_a_payload_with_no_nul_is_untouched_by_the_check():
    """The negative control. A rule that refuses everything is not a rule."""
    parsed = p(content="perfectly ordinary text", title="A Title")
    assert parsed.content == "perfectly ordinary text"
    assert "\x00" not in parsed.body()
