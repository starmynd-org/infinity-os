"""Nine tools, each a thin wrapper over a verb, and that is checked rather than claimed.

The load-bearing test is `test_every_write_tool_calls_its_declared_verb_and_no_other`. It
monkeypatches `store.apply`, calls each write tool, and asserts the tool entered exactly one
transition and that it was the one the tool declares it wraps. A tool that grew a second write,
or that computed a state change of its own, fails here.

Run:  python3 -m mcp.tests.test_tools
"""

from __future__ import annotations

import sys
import traceback

import os as _os
import sys as _sys
_R = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
for _p in (_R, _os.path.join(_R, 'engine'), _os.path.join(_R, 'queue')):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import store
from mcp import tools

from swarm_engine import transitions as _engine_t                       # noqa: F401
from swarm_engine import accept as _engine_a                            # noqa: F401
from fabric import emit as _fabric                                      # noqa: F401

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"ok    {name}")
    except AssertionError as exc:
        FAIL.append((name, str(exc)))
        print(f"FAIL  {name}\n      {exc}")
    except Exception:                                                   # noqa: BLE001
        FAIL.append((name, traceback.format_exc()))
        print(f"ERROR {name}\n{traceback.format_exc()}")


class Spy:
    def __init__(self, ret=None):
        self.calls = []
        self.ret = ret if ret is not None else {"id": "0001", "spy": True}

    def __call__(self, verb, *a, **kw):
        self.calls.append(verb)
        return self.ret


def test_there_are_exactly_nine():
    assert len(tools.TOOLS) == 9, f"{len(tools.TOOLS)} tools, expected nine"
    assert set(tools.TOOLS) == {
        "emit_event", "read_queue", "claim_work", "report_progress", "finish_work",
        "raise_question", "read_recommendations", "book_receipt", "resolve_entity"}


def test_every_declared_verb_is_registered_or_owned_by_a_named_lane():
    registered = set(store.registered())
    for name, t in tools.TOOLS.items():
        verb = t.get("verb")
        if not verb:
            continue
        if verb in registered:
            continue
        assert t.get("owner"), (
            f"{name} declares the verb {verb!r}, which nothing registers and no lane owns. "
            f"That is a tool implementing its own transition.")


def test_every_write_tool_calls_its_declared_verb_and_no_other():
    real = store.transitions.apply
    cases = {
        "claim_work": ({"lanes": ["qc"], "agent": "T4"}, ["claim"]),
        "report_progress": ({"agent": "T4", "status": "working"}, ["heartbeat"]),
        "finish_work": ({"task": "0001", "outcome": "done", "summary": "x", "agent": "T4"},
                        ["done"]),
        "raise_question": ({"question": "q", "default": "d", "agent": "T4"}, ["ask"]),
    }
    for name, (args, expect) in cases.items():
        spy = Spy()
        store.apply = spy
        tools.store.apply = spy
        try:
            tools.call(name, args)
        finally:
            store.apply = real
            tools.store.apply = real
        assert spy.calls == expect, f"{name} called {spy.calls}, expected {expect}"


def test_report_progress_writes_two_verbs_only_when_it_has_two_things_to_say():
    real = store.transitions.apply
    spy = Spy()
    store.apply = spy
    tools.store.apply = spy
    try:
        tools.call("report_progress", {"agent": "T4", "task": "0001", "note": "found a trap"})
    finally:
        store.apply = real
        tools.store.apply = real
    assert spy.calls == ["heartbeat", "note"], (
        f"expected the heartbeat and the note as two registered transitions, got {spy.calls}. "
        f"Merging them into one would be a state change that exists only in this tool.")


def test_finish_work_maps_outcome_to_the_right_verb():
    real = store.transitions.apply
    for outcome, verb in (("done", "done"), ("blocked", "block"), ("failed", "fail")):
        spy = Spy()
        store.apply = spy
        tools.store.apply = spy
        try:
            tools.call("finish_work", {"task": "0001", "outcome": outcome, "summary": "x",
                                       "agent": "T4"})
        finally:
            store.apply = real
            tools.store.apply = real
        assert spy.calls == [verb], f"{outcome} called {spy.calls}, expected [{verb!r}]"
    try:
        tools.call("finish_work", {"task": "0001", "outcome": "mostly done", "summary": "x",
                                   "agent": "T4"})
    except ValueError as exc:
        assert "partly done" in str(exc)
    else:
        raise AssertionError("`mostly done` was accepted as an outcome")


def test_no_tool_accepts_acceptance():
    """Acceptance is the human's act. An MCP `accept work` is self-approval one call removed."""
    for name, t in tools.TOOLS.items():
        assert t.get("verb") != "accept work", f"{name} wraps `accept work`"
        assert "accept" not in name, f"{name} names acceptance"
    assert "accept_work" not in tools.TOOLS


def test_nothing_touching_config_or_permission_mode_is_reachable():
    for name, t in tools.TOOLS.items():
        props = (t["schema"].get("properties") or {})
        for prop in props:
            for f in tools.FORBIDDEN:
                assert f not in prop.lower(), f"{name} exposes {prop!r}"
        for f in tools.FORBIDDEN:
            assert f not in name.lower(), f"the tool name {name!r} matches {f!r}"
    # And at the door, in case a caller passes one anyway.
    for bad in ({"permission_mode": "bypassPermissions"}, {"config": {}},
                {"allowed_tools": "*"}, {"engine_args": "--dangerously"}):
        try:
            tools.call("read_queue", bad)
        except ValueError as exc:
            assert "refused" in str(exc)
        else:
            raise AssertionError(f"{bad} was not refused")


def test_there_is_no_generic_run_a_command_tool():
    for name in tools.TOOLS:
        assert name not in ("run", "exec", "run_verb", "apply", "call", "shell", "bash")
    try:
        tools.call("run_verb", {"verb": "pause"})
    except KeyError as exc:
        assert "no generic run-a-verb tool" in str(exc)
    else:
        raise AssertionError("a generic tool was dispatched")


def test_raise_question_refuses_a_missing_default():
    try:
        tools.call("raise_question", {"question": "x", "default": "  ", "agent": "T4"})
    except ValueError as exc:
        assert "default" in str(exc)
    else:
        raise AssertionError("a question with no default was accepted")


def test_emit_event_does_not_default_the_flags():
    """`external` and `canon_touching` are required in the schema, so a producer that forgets is
    refused rather than silently emitting an ungated event."""
    req = tools.TOOLS["emit_event"]["schema"]["required"]
    assert "external" in req and "canon_touching" in req
    try:
        tools.call("emit_event", {"type": "session.started", "agent": "T4"})
    except TypeError:
        pass
    else:
        raise AssertionError("emit_event ran without the flags")


def test_every_tool_call_is_attributed():
    for name in ("claim_work", "report_progress", "finish_work", "raise_question"):
        try:
            args = {"lanes": [], "task": "0001", "outcome": "done", "summary": "x",
                    "question": "q", "default": "d"}
            tools.call(name, {k: v for k, v in args.items()})
        except ValueError as exc:
            assert "agent name" in str(exc), f"{name}: {exc}"
        else:
            raise AssertionError(f"{name} ran unattributed")


def test_the_server_process_can_write():
    """Run the server AS A SUBPROCESS and make it write, because that is a different process.

    This test exists because a real Claude Code session found the bug the other tests could not:
    transition registration is an import side effect, `mcp/tools.py` did not import the lanes,
    and every write tool failed with `no transition named 'ask'. Registered: (none registered)`
    while every read tool worked. The unit tests above all passed, because a test module that
    imports the lanes in order to enumerate them has already registered them. The test process
    was never the server process. This one is.
    """
    import json as _json
    import os as _os
    import subprocess as _sp
    repo = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    db = _os.environ.get("BRAIN_PG_DB", "brain")
    assert db != "brain", "run this against a scratch database; it writes a real question"
    msgs = "\n".join([
        _json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        _json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "raise_question",
            "arguments": {"question": "does the server process hold the registry?",
                          "default": "treat it as answered no", "agent": "test-harness"}}}),
    ]) + "\n"
    out = _sp.run([sys.executable, "-m", "mcp.server"], input=msgs, capture_output=True,
                  text=True, cwd=repo, env={**_os.environ, "BRAIN_PG_DB": db}, timeout=90)
    replies = [_json.loads(l) for l in out.stdout.splitlines() if l.strip()]
    call = next(r for r in replies if r.get("id") == 2)
    text = call["result"]["content"][0]["text"]
    assert not call["result"].get("isError"), (
        f"the server process could not run a write tool: {text}. Transition registration is an "
        f"import side effect; the server must import the lanes it wraps.")
    assert '"id"' in text and "q" in text, f"no question id came back: {text}"


def main() -> int:
    import os
    os.environ.pop("SWARM_AGENT", None)
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            check(name, fn)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
