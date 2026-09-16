"""Claude Code and Codex, specifically. Andrew named both, so both are named here.

> *"Can we get Claude Code to register? Codex to register?"*

**THE POINT OF THIS FILE IS THAT REGISTRATION CARRIES A FACT INSTEAD OF A CLAIM.**

`registry.send_registration` takes a name and a harness and records them as self-reports, which is
correct and is all it can do: a process saying "I am a Claude Code session" is a string. **This
module does one thing more — it binds a registration to a TRANSCRIPT FILE ON DISK, and checks that
the file is there before saying so.** After that, "which conversation is this agent" stops being a
label the agent chose and becomes a path anybody can `stat`.

**And when it cannot bind one, it says `verified=False` and gives the reason.** It never invents a
pointer, never guesses a session id, and never reports an unverified pointer as a verified one.
*It fabricates nothing* is the property the existing capture hook states about itself, and this is
the same property in a different place.

THE TWO HARNESSES, AS THEY ACTUALLY LEAVE STATE ON THIS MACHINE

    claude-code   <claude home>/projects/<encoded cwd>/<session-uuid>.jsonl
                  session id from CLAUDE_SESSION_ID, or from a transcript path's own stem.
                  Note there is MORE THAN ONE Claude home on this machine and they do not share
                  state, so the home is an argument and this module guesses none.

    codex         <codex home>/sessions/YYYY/MM/DD/rollout-<ISO8601>-<uuid>.jsonl
                  session id is the uuid tail of the rollout filename. Verified by measurement:
                  531 files under ~/.codex/sessions on the WSL install, all of that shape.

**LOOPBACK ONLY, AND IT IS STRUCTURAL RATHER THAN ENFORCED.** Everything here is `os.stat` and
`os.path`. There is no socket, no host, no port and no URL in this module, so there is nothing for
a cross-host question to attach to. Registration remains a local file placed in a local directory.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass, field

CLAUDE_CODE = "claude-code"
CODEX = "codex"

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

#: Environment names a Claude Code child process may find its session id under, in preference
#: order, **and the list exists because I first wrote one name and then measured.**
#:
#: My first version read only `CLAUDE_SESSION_ID`, found it unset, and had this module report that
#: the harness *"does not publish its session id to child processes"* -- a statement about the
#: harness inferred from one variable name. **`CLAUDE_CODE_SESSION_ID` was set the whole time, and
#: its value is exactly this session's transcript filename**, measured in a live child process on
#: 2026-09-09. The wrong sentence was available from a true reading, which is the whole family of
#: error this operation keeps paying for: *read the match, not the count.*
#:
#: `CLAUDE_CODE_HOST_SESSION_ID` is deliberately NOT in this list. It is also set, and it carries a
#: DIFFERENT id of the form `local_<uuid>` which does not name a transcript. Two ids that both look
#: right is exactly how a registry ends up bound to the wrong one, so `detected_from` records which
#: name supplied the value and the surface can show it.
CLAUDE_SESSION_ENV = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")

#: `rollout-<ISO8601 with dashes>-<uuid>.jsonl`, anchored at both ends so a `.bak` beside one is
#: not read as a session.
_ROLLOUT = re.compile(r"^rollout-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-(" + _UUID.pattern +
                      r")\.jsonl$", re.I)


@dataclass
class HarnessSession:
    """What a registering session can say about itself, split into what is CHECKED and what is not.

    **`verified` governs how every other field may be quoted.** A record with `verified=False`
    carries a pointer nobody has confirmed, and the `notes` say why. It is still worth recording --
    an unverified pointer is a lead -- but it is not a fact and this dataclass will not let a call
    site forget which it is holding.
    """

    harness: str
    session_id: str
    transcript_path: str = ""
    verified: bool = False
    transcript_bytes: int = -1
    transcript_mtime_utc: str = ""
    workdir: str = ""
    detected_from: str = "unknown"
    notes: list[str] = field(default_factory=list)

    def as_body(self) -> str:
        """The registration message body. Every line is either a checked fact or labelled as not."""
        lines = [
            f"HARNESS: {self.harness}",
            f"SESSION-ID: {self.session_id or '(none)'}",
            f"WORKDIR: {self.workdir or '(unknown)'}",
            f"DETECTED-FROM: {self.detected_from}",
            f"TRANSCRIPT: {self.transcript_path or '(none)'}",
            f"TRANSCRIPT-VERIFIED: {'yes' if self.verified else 'NO'}",
            f"TRANSCRIPT-BYTES: {self.transcript_bytes}",
            f"TRANSCRIPT-MTIME-UTC: {self.transcript_mtime_utc or '(none)'}",
            "",
        ]
        if self.verified:
            lines.append("The transcript pointer above was stat'd on this host at registration and")
            lines.append("existed. Which conversation this agent is, is a path you can check.")
        else:
            lines.append("THE TRANSCRIPT POINTER IS NOT VERIFIED. Do not quote it as one. Reasons:")
        for note in self.notes:
            lines.append(f"  - {note}")
        lines.append("")
        lines.append("Every value here except the verified transcript stat is a SELF-REPORT and is")
        lines.append("recorded as one. Registering is a request; it is not authorization.")
        return "\n".join(lines) + "\n"


def _stat(path: str, session: HarnessSession) -> None:
    """Bind the pointer, or say why not. **The only place `verified` is ever set to True.**"""
    if not path:
        session.notes.append("no transcript path was supplied and none could be derived")
        return
    session.transcript_path = os.path.abspath(path)
    if not os.path.isfile(session.transcript_path):
        session.notes.append(f"no file at {session.transcript_path}")
        return
    stat = os.stat(session.transcript_path)
    session.transcript_bytes = stat.st_size
    session.transcript_mtime_utc = _dt.datetime.fromtimestamp(
        stat.st_mtime, _dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session.verified = True


def encode_project_dir(workdir: str) -> str:
    """Claude Code's own encoding of a working directory into a `projects/` subdirectory name.

    `C:\\Users\\you\\repos` becomes `C--Users-you-repos`. Derived from the directory names on
    disk rather than from documentation, and **this is a REPRODUCTION of another program's rule, so
    it is used only to LOOK, never to assert.** A miss returns no pointer and says so; it never
    returns a path it made up.

    **EACH non-alphanumeric character becomes ONE hyphen. Runs are NOT collapsed, and getting that
    wrong is how this function failed its first run.** I wrote `[^A-Za-z0-9]+` and produced
    `C-Users-you-repos` for a directory that is really `C--Users-you-repos`: the `:` and the
    `\\` are two characters and they are two hyphens. **It was off by exactly one character and the
    only reason it did not ship is that the proof binds against real directories on this disk
    rather than against a fixture I would have written to match my own regex.**
    """
    return re.sub(r"[^A-Za-z0-9]", "-", workdir.rstrip("\\/"))


def claude_code_session(*, claude_home: str, session_id: str = "", transcript_path: str = "",
                        workdir: str = "") -> HarnessSession:
    """Describe a Claude Code session. `claude_home` is required and is never guessed.

    **It is required because this machine has more than one Claude Code installation and they do
    not share a `~/.claude`.** A default here would silently pick one and produce a confident
    answer about the wrong install -- which is the exact shape of the coverage error
    `web/chats/bin/capture_coverage.py` exists to avoid.
    """
    workdir = workdir or os.getcwd()
    session = HarnessSession(harness=CLAUDE_CODE, session_id=session_id, workdir=workdir)

    if transcript_path:
        session.detected_from = "argument"
        if not session.session_id:
            found = _UUID.search(os.path.basename(transcript_path))
            if found:
                session.session_id = found.group(0).lower()
                session.notes.append("session id taken from the transcript filename")
        _stat(transcript_path, session)
        return session

    if not session.session_id:
        for name in CLAUDE_SESSION_ENV:
            value = (os.environ.get(name) or "").strip()
            if value:
                session.session_id, session.detected_from = value, name
                break
    else:
        session.detected_from = "argument"

    if not session.session_id:
        session.notes.append(
            f"no session id: none supplied and none of {', '.join(CLAUDE_SESSION_ENV)} is set in "
            f"this process's environment. That is a statement about THIS environment, not about "
            f"the harness in general.")
        return session

    candidate = os.path.join(claude_home, "projects", encode_project_dir(workdir),
                             f"{session.session_id}.jsonl")
    _stat(candidate, session)
    if not session.verified:
        session.notes.append(
            f"the pointer was DERIVED from claude_home + the encoded workdir + the session id, and "
            f"the derivation is a reproduction of another program's naming rule. A miss here means "
            f"the rule or the home is wrong, not that the session does not exist.")
    return session


def codex_session(*, codex_home: str, session_id: str = "",
                  transcript_path: str = "", workdir: str = "") -> HarnessSession:
    """Describe a Codex session from a rollout file, or find the rollout for a session id."""
    workdir = workdir or os.getcwd()
    session = HarnessSession(harness=CODEX, session_id=session_id, workdir=workdir)

    if transcript_path:
        session.detected_from = "argument"
        match = _ROLLOUT.match(os.path.basename(transcript_path))
        if match and not session.session_id:
            session.session_id = match.group(2).lower()
            session.notes.append("session id taken from the rollout filename")
        elif not match:
            session.notes.append(
                f"{os.path.basename(transcript_path)!r} is not of the form "
                f"rollout-<ISO8601>-<uuid>.jsonl; recorded, and the id was not derived from it")
        _stat(transcript_path, session)
        return session

    if not session.session_id:
        session.notes.append("no session id supplied and no rollout path given; nothing to bind")
        return session
    session.detected_from = "argument"

    # Codex files a rollout under sessions/YYYY/MM/DD, so the id alone does not give a path. Walk
    # for it. Bounded: this is a per-registration cost on a directory of ~531 files, measured.
    root = os.path.join(codex_home, "sessions")
    if not os.path.isdir(root):
        session.notes.append(f"no rollout directory at {root}")
        return session
    wanted = session.session_id.lower()
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            match = _ROLLOUT.match(name)
            if match and match.group(2).lower() == wanted:
                _stat(os.path.join(dirpath, name), session)
                session.notes.append("rollout located by walking sessions/YYYY/MM/DD")
                return session
    session.notes.append(f"no rollout under {root} carries session id {wanted}")
    return session


def detect(*, claude_home: str = "", codex_home: str = "") -> HarnessSession | None:
    """Guess the harness from the environment. Returns `None` rather than guessing wrong.

    **A `None` here is a real answer.** A registration that has to be told what it is, is better
    than one that decided from an environment variable that happened to be set.
    """
    if claude_home and any(os.environ.get(n) for n in CLAUDE_SESSION_ENV):
        return claude_code_session(claude_home=claude_home)
    if os.environ.get("CODEX_SESSION_ID") and codex_home:
        return codex_session(codex_home=codex_home,
                             session_id=os.environ["CODEX_SESSION_ID"].strip())
    return None
