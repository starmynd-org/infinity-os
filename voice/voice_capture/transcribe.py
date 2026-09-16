"""Speech to text, and the rule that it never invents any.

THE ONE RULE. Unintelligible audio is a NULL, not a guess. D3 measured this on `stated_goal`:
*a fabricated goal is worse than a null one, because it will be believed later.* Every path in
this file that cannot produce text produces an empty string and a status that says why, and the
store refuses a non-empty body under either of those statuses so the rule survives a caller who
does not read this docstring.

THE TWO ENGINES ON THIS HOST, BOTH MEASURED, on the operator's real 2026-08-17 monologue, in one
session, on the identical wav, by ONE scorer -- so the gap below is a gap and not an artifact of
two different rulers:

    whisper.cpp small.en   WER 0.0138    12 errors / 871 words   15 of 16 proper nouns   DEFAULT
    windows-sapi           WER 0.1481   129 errors / 871 words    8 of 16 proper nouns   FALLBACK

BOTH ARE LOCAL, FREE AND OFFLINE. Nothing leaves the machine at transcription time under either;
whisper costs a ONE-TIME model download and nothing per recording. That distinction is the whole
reason `q0402` was answerable without the operator, and the hosted APIs are still NOT BUILT
because they would send his business planning to a third party every morning and spend his money
to do it. That decision is his and it is still open. See `voice/docs/ENGINES.md`.

WHY THE PROPER-NOUN NUMBER IS REPORTED SEPARATELY FROM THE WORD NUMBER. 86% word accuracy sounds
survivable and it was not: the errors concentrated on exactly the highest-value tokens. SAPI
rendered a client's name as "a lively" and then "gladly", and dropped
`TLDV`, `VSL`, `QC` and `starmynd.com` entirely. A transcript that gets the shape of a day right
while destroying every client and product name in it is one whose most load-bearing content is
its least reliable part -- and a proper noun turned into a different real word does not read as
an error, it reads as a fact. That is why `accuracy_class` travels with the text alongside
`transcription_status`: status says whether there ARE words, class says whether you may resolve
a name out of them, and on this system those had different answers.

`transcription_confidence` travels with the text and `CONFIDENCE_OK_FLOOR` demotes a
low-confidence pass to `partial` rather than calling it `ok`. Whisper's real speech scores ~0.97
so it earns `ok` honestly, where SAPI at ~0.65-0.79 mostly earned `partial`.

THE REGISTRY. Engines are tried in order and each one says whether it is available before it is
asked to do anything. An engine that is not installed produces `unavailable`, which is a
different act for the operator than `null`: `unavailable` is "install something", `null` is
"re-record, the room was too loud". Collapsing them would tell him to do the wrong thing.

THE FALLBACK IS LOAD-BEARING. A missing whisper model is `unavailable`, which falls through to
`windows-sapi`, which needs no model and no build. So the worst case of the better engine being
absent is the accuracy this system already shipped -- never a capture that fails to land.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import config

#: The four honest outcomes, same vocabulary as `brain.objective` and `brain.voice_capture`.
OK, PARTIAL, NULL, UNAVAILABLE = "ok", "partial", "null", "unavailable"

#: WHAT A BODY FROM THIS ENGINE MAY BE USED FOR. A second axis, deliberately not folded into
#: `transcription_status`, because 0405 measured that the two come apart: `windows-sapi` returns
#: `status='ok'` on text in which SIX of sixteen key proper nouns are gone. Status said the
#: transcript was whole. It was whole and it was unusable for the one thing a downstream agent
#: would do with it, which is look a name up.
#:
#: `names-reliable` -- proper nouns survive. Safe to resolve an entity from this body.
#: `shape-only`     -- the shape of the day survives and the NAMES IN IT DO NOT. Safe for "what
#:                     was Monday about", unsafe as the input to anything that resolves a client,
#:                     a product or an acronym.
#: `unmeasured`     -- an engine nobody has scored on this host. Never assume either of the above.
ACCURACY_NAMES_RELIABLE = "names-reliable"
ACCURACY_SHAPE_ONLY = "shape-only"
ACCURACY_UNMEASURED = "unmeasured"


@dataclass
class Transcription:
    engine: str
    status: str
    text: str
    confidence: float | None
    segments: int
    detail: str = ""
    #: WHAT THIS BODY MAY BE USED FOR, measured rather than asserted. See `ACCURACY_CLASS`.
    #: It travels with the transcript because `transcription_status` answers "is there text and
    #: is it whole" and does NOT answer "can I resolve a client name out of it" -- and on this
    #: system those are different questions with different answers.
    accuracy_class: str = ACCURACY_UNMEASURED

    def __post_init__(self):
        # A last structural guard, on top of the two in the store. Every caller in this package
        # goes through this class, so a backend that returned words alongside a no-text status
        # cannot get them past this point.
        if self.status in (NULL, UNAVAILABLE) and self.text:
            raise ValueError(
                f"engine {self.engine!r} returned status {self.status!r} with "
                f"{len(self.text)} characters of text. That combination is a fabrication and it "
                f"is refused here, in the store's CHECK, and in the intake verb.")


class WindowsSapi:
    """The local Windows recogniser. No network, no cost, no model download, mediocre.

    KEPT AS THE FALLBACK, not superseded. It needs no model file and no build, so it is what
    answers when whisper's model or binary is missing. `q0402` was answered ATTEMPT B, TIMEBOXED,
    KEEP A AS THE FALLBACK, and this class is the A that stays.
    """

    name = "windows-sapi"
    timeout_ratio = config.TRANSCRIBE_TIMEOUT_RATIO
    #: MEASURED on this host 2026-08-18 by task 0405, on the operator's real 2026-08-17
    #: monologue: WER 0.1481, 129 word errors in 871 words, and **8 of 16 key proper nouns
    #: recovered**. It lost a client's name to "a lively", `Infinity` to "Infiniti",
    #: and `TLDV`, `VSL`, `QC`, `Matt`, `AI` and `starmynd.com` entirely.
    accuracy_class = ACCURACY_SHAPE_ONLY

    def available(self) -> tuple[bool, str]:
        try:
            p = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command",
                 "try { Add-Type -AssemblyName System.Speech -ErrorAction Stop; "
                 "$r=[System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers(); "
                 "Write-Output $r.Count } catch { Write-Output 'ERR' }"],
                capture_output=True, text=True, timeout=90)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"powershell.exe is not reachable from here ({exc.__class__.__name__})"
        out = p.stdout.replace("\r", "").strip().splitlines()
        count = out[-1] if out else ""
        if count == "ERR" or not count.isdigit():
            return False, "System.Speech did not load on the Windows host"
        if int(count) < 1:
            return False, "no speech recognizer is installed on the Windows host"
        return True, f"{count} installed recognizer(s)"

    def transcribe(self, wav: Path, timeout_s: int) -> Transcription:
        script = Path(__file__).resolve().parent.parent / "bin" / "transcribe-windows.ps1"
        try:
            p = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", config.windows_path(script),
                 "-WavFile", config.windows_path(wav)],
                capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return Transcription(self.name, UNAVAILABLE, "", None, 0,
                                 f"the recognizer did not return within {timeout_s}s")
        raw = p.stdout.replace("\r", "").strip()
        line = raw.splitlines()[-1] if raw else ""
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            # Unparseable output is UNAVAILABLE and never a salvage attempt. Regexing words out
            # of a broken result is precisely how a fabricated transcript gets born.
            return Transcription(
                self.name, UNAVAILABLE, "", None, 0,
                f"the recognizer's output was not JSON (rc={p.returncode}): "
                f"{(raw or p.stderr)[:300]!r}")

        status = d.get("status") or UNAVAILABLE
        text = (d.get("text") or "").strip()
        conf = d.get("mean_confidence")
        if status in (NULL, UNAVAILABLE):
            text = ""
        elif conf is not None and float(conf) < config.CONFIDENCE_NULL_FLOOR:
            # THE ENGINE DOES NOT STAND BEHIND THIS AND NEITHER DO WE. Discarded to `null` with
            # an empty body, and what was discarded is stated rather than quietly dropped: the
            # audio is retained and hashed, so this costs nothing that a better engine cannot
            # recover, and keeping it would cost the one thing that cannot be recovered, which is
            # a reader's ability to believe the transcripts that are real.
            d["error"] = (
                f"discarded {len(text)} characters at mean confidence {conf}, below the "
                f"{config.CONFIDENCE_NULL_FLOOR} floor. Reported as null: unintelligible audio "
                f"is a null, not a guess. The audio is retained and can be transcribed again."
            )
            status, text = NULL, ""
        elif status == OK and conf is not None and float(conf) < config.CONFIDENCE_OK_FLOOR:
            # Demoted, not rejected. The words are real and the engine is not confident in them,
            # and `partial` is the vocabulary's word for "there is text and do not trust it whole".
            status = PARTIAL
            d["error"] = (d.get("error") or "") + (
                f" mean confidence {conf} is below the {config.CONFIDENCE_OK_FLOOR} floor, so "
                f"this is reported partial rather than ok")
        return Transcription(d.get("engine") or self.name, status, text,
                             float(conf) if conf is not None else None,
                             int(d.get("segments") or 0), (d.get("error") or "").strip(),
                             self.accuracy_class)


class WhisperCpp:
    """whisper.cpp with a local ggml model. Local, free, offline, and it keeps the names.

    ANSWERS `q0402` OPTION B. Built and measured on this host 2026-08-18 by task 0405, against
    the identical sample `windows-sapi` was measured on, scored by one scorer so the delta is a
    delta and not an artifact of two rulers:

        windows-sapi          WER 0.1481   129 errors / 871 words    8 of 16 proper nouns
        whisper.cpp small.en  WER 0.0138    12 errors / 871 words   15 of 16 proper nouns

    NOTHING LEAVES THE MACHINE AT TRANSCRIPTION TIME, and that is the entire reason B is
    acceptable where the paid remote APIs are not. The model is a ONE-TIME download; transcription
    is a local process reading a local file. Demonstrated, not asserted: the measurement above was
    produced inside `unshare -rn`, a network namespace with no interface but a down `lo`, in which
    `curl https://huggingface.co` returns rc=6, could not resolve host. The engine never opens a
    socket, so removing the network changes nothing about its output.

    THE ONE NAME IT STILL LOSES is the client's, rendered as one consistent misspelling. That is a different and much
    weaker failure than SAPI's: SAPI produced "a lively" and then "gladly", two DIFFERENT REAL
    ENGLISH WORDS that read as facts and cannot be found by searching for the client. Whisper
    produces one consistent phonetic spelling in all three places, which a reader recognises and
    a substitution fixes. It is still a miss and it is counted as one.
    """

    name = "whisper.cpp"
    #: 3.0x the audio's own duration plus a floor. The MEASURED ratio on this host is 0.50
    #: (158.2 s for 316.44 s), so this is 6x headroom -- the number that matters is the one on a
    #: loaded box, not the one on an idle one.
    timeout_ratio = config.WHISPER_TIMEOUT_RATIO
    #: MEASURED, see the class docstring. 15 of 16 key proper nouns on the operator's real
    #: monologue, including every acronym and the domain.
    accuracy_class = ACCURACY_NAMES_RELIABLE

    def available(self) -> tuple[bool, str]:
        """Binary AND model, both, before anything is claimed.

        A MISSING MODEL IS `unavailable`, NOT AN ERROR. It falls through to `windows-sapi`, which
        needs no model, so the worst case of whisper not being installed is the accuracy this
        system already shipped and never a capture that fails to land.
        """
        if not config.WHISPER_BIN.exists():
            return False, f"no whisper-cli at {config.WHISPER_BIN} (windows-sapi will take it)"
        if not os.access(config.WHISPER_BIN, os.X_OK):
            return False, f"{config.WHISPER_BIN} is not executable"
        if not config.WHISPER_MODEL.exists():
            return False, (f"no model at {config.WHISPER_MODEL} -- download it, see "
                           f"voice/docs/ENGINES.md (windows-sapi will take it meanwhile)")
        mb = config.WHISPER_MODEL.stat().st_size / 1e6
        if mb < 1:
            return False, f"model at {config.WHISPER_MODEL} is {mb:.3f} MB, which is a truncated download"
        return True, f"{config.WHISPER_MODEL.name}, {mb:.0f} MB, {config.WHISPER_THREADS} threads"

    def transcribe(self, wav: Path, timeout_s: int) -> Transcription:
        with tempfile.TemporaryDirectory(prefix="voice-whisper-") as td:
            out = Path(td) / "out"
            # DO NOT ADD `-nt` HERE. It looks like a tidy-up -- we parse JSON and join the
            # segment texts, so the printed timestamps are noise we never read -- and it MEASURABLY
            # DESTROYS THE TRANSCRIPT. Measured 2026-08-18 on the operator's monologue, same
            # binary, same model, same file, the ONLY difference being this flag:
            #     without -nt   WER 0.0138   12 errors / 871 words   15 of 16 proper nouns
            #     with    -nt   WER 0.1102   96 errors / 871 words   14 of 16, and 79 WORDS GONE
            # Whisper generates timestamp tokens as part of its output sequence; suppressing them
            # changes the decode path and collapsed 77 segments into 11, silently dropping a whole
            # clause about date ranges and analysis. The words were not marked missing anywhere --
            # they were simply absent, which is the failure this lane exists to make impossible.
            try:
                p = subprocess.run(
                    [str(config.WHISPER_BIN), "-m", str(config.WHISPER_MODEL),
                     "-f", str(wav), "-t", str(config.WHISPER_THREADS),
                     "-ojf", "-of", str(out)],
                    capture_output=True, text=True, timeout=timeout_s)
            except subprocess.TimeoutExpired:
                return Transcription(self.name, UNAVAILABLE, "", None, 0,
                                     f"whisper-cli did not return within {timeout_s}s")
            except OSError as exc:
                return Transcription(self.name, UNAVAILABLE, "", None, 0,
                                     f"whisper-cli could not be run: {exc}")
            jf = out.with_suffix(".json")
            if p.returncode != 0 or not jf.exists():
                # No salvage from stdout. The transcript text is printed there and scraping it
                # would produce words with no probabilities attached, which is a body that cannot
                # be checked against either floor -- exactly the shape this file refuses.
                return Transcription(
                    self.name, UNAVAILABLE, "", None, 0,
                    f"whisper-cli exited {p.returncode} with no JSON: {(p.stderr or '')[-300:]!r}")
            try:
                d = json.loads(jf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                return Transcription(self.name, UNAVAILABLE, "", None, 0,
                                     f"whisper-cli's JSON did not parse: {exc}")

        segs = d.get("transcription") or []
        raw = "".join(s.get("text") or "" for s in segs).strip()
        # `[_BEG_]`, `[_TT_n]` and friends are whisper's own control tokens, not predictions
        # about the audio, and averaging them in would move the number without measuring anything.
        probs = [t["p"] for s in segs for t in (s.get("tokens") or [])
                 if isinstance(t.get("p"), (int, float)) and not t.get("text", "").startswith("[_")]
        conf = sum(probs) / len(probs) if probs else None

        # THE NON-SPEECH GUARD, AND IT IS NOT THE CONFIDENCE FLOOR WEARING A HAT.
        #
        # Measured 2026-08-18 on the two silent-room captures that made `CONFIDENCE_NULL_FLOOR`
        # exist: whisper answered `[ Background noise ]` at mean token probability 0.5798 and
        # `[MUSIC PLAYING]` at 0.7205. BOTH ARE FAR ABOVE THE 0.20 FLOOR, and the second is
        # nearly at the 0.75 `ok` floor. The floor cannot catch these and was never built to --
        # the model is not unconfident, it is confident there was music. Low confidence and
        # non-speech are different failures and only one of them is a number.
        #
        # Stored unguarded, `[MUSIC PLAYING]` becomes a sentence the operator supposedly spoke:
        # the same defect class as a fabricated transcription, refused the same way. Annotations
        # are removed from the body; a body that is NOTHING BUT annotation is `null` and empty.
        # Real words that merely shared a segment with an annotation are kept, because discarding
        # them would be a second way to lose what he actually said.
        spoken = config.WHISPER_ANNOTATION_RE.sub(" ", raw)
        spoken = " ".join(spoken.split())
        if raw and not spoken:
            return Transcription(
                self.name, NULL, "", conf, len(segs),
                f"the model returned {len(raw)} characters and ALL of it was non-speech "
                f"annotation ({raw[:120]!r}) at mean token probability {conf}. That is a label "
                f"for the room, not something the operator said, and storing it would be a "
                f"fabrication that reads as a fact. Reported null; the audio is retained and "
                f"can be transcribed again.")
        if not spoken:
            return Transcription(self.name, NULL, "", conf, len(segs),
                                 "the model returned no text at all for this audio")

        detail = ""
        if raw != spoken:
            # Never silent. Something was removed and the reader is told what and why.
            detail = (f"removed non-speech annotation from the body: "
                      f"{config.WHISPER_ANNOTATION_RE.findall(raw)[:5]}")

        status = OK
        if conf is not None and conf < config.CONFIDENCE_NULL_FLOOR:
            return Transcription(
                self.name, NULL, "", conf, len(segs),
                f"discarded {len(spoken)} characters at mean token probability {conf}, below "
                f"the {config.CONFIDENCE_NULL_FLOOR} floor. Reported as null: unintelligible "
                f"audio is a null, not a guess. The audio is retained.")
        if conf is not None and conf < config.CONFIDENCE_OK_FLOOR:
            status = PARTIAL
            detail = (detail + f" mean token probability {conf} is below the "
                               f"{config.CONFIDENCE_OK_FLOOR} floor, so this is reported partial "
                               f"rather than ok").strip()
        return Transcription(self.name, status, spoken, conf, len(segs), detail.strip(),
                             self.accuracy_class)


#: IN ORDER, AND THE ORDER IS THE ANSWER TO `q0402`. whisper.cpp first because it is the one
#: that keeps the operator's client's name; `windows-sapi` second because it needs no model file
#: and is therefore what still works on a host where the model was never downloaded. A second
#: engine is a class with the same two methods and nothing else in this package changes.
#:
#: THE FALLBACK IS THE POINT, not a leftover. If whisper's binary or model goes missing the
#: capture still lands, at the accuracy this system already shipped, and `voice engines` says
#: which one is answering and what it is worth.
ENGINES = [WhisperCpp(), WindowsSapi()]


def pending_engine(engines=None):
    """The engine that would actually take the next capture, or None.

    Exists because the CALLER has to write a `due_at` for the transcribe stage BEFORE the stage
    runs, and the two engines on this host do not run at the same speed: SAPI did 316.44 s of
    audio in 12.2 s and whisper did it in 158.2 s. A deadline computed from SAPI's ratio and then
    spent on whisper is a capture reported STUCK for being slow, which is the stuck signal --
    the one thing here that catches an outage nobody raised -- crying wolf about normal work.
    """
    for eng in (engines if engines is not None else ENGINES):
        ok, _ = eng.available()
        if ok:
            return eng
    return None


def timeout_ratio(engines=None) -> float:
    """The ratio the engine that will actually run asks for. SAPI's if none is available, which
    is the conservative reading: no engine means the stage returns `unavailable` immediately."""
    eng = pending_engine(engines)
    return getattr(eng, "timeout_ratio", config.TRANSCRIBE_TIMEOUT_RATIO)


def transcribe(wav: Path, duration_s: float = 0.0, engines=None) -> Transcription:
    """First available engine wins. No available engine is `unavailable`, loudly.

    The timeout scales with the audio, because SAPI runs a little faster than real time and a
    20-minute monologue judged by a 20-second note's deadline would be killed mid-file and
    reported as a failure that was really a budget.
    """
    tried = []
    for eng in (engines if engines is not None else ENGINES):
        ok, why = eng.available()
        if not ok:
            tried.append(f"{eng.name}: {why}")
            continue
        # PER ENGINE, because they do not run at the same speed and one deadline for both would
        # be either useless for SAPI or a guillotine for whisper. Measured on this host on the
        # same 316.44 s file: SAPI 12.2 s (26x faster than real time), whisper 158.2 s (2x).
        ratio = getattr(eng, "timeout_ratio", config.TRANSCRIBE_TIMEOUT_RATIO)
        timeout_s = max(config.TRANSCRIBE_TIMEOUT_FLOOR_S, int(duration_s * ratio) + 60)
        return eng.transcribe(wav, timeout_s)
    return Transcription(
        "none", UNAVAILABLE, "", None, 0,
        "no transcription engine is available on this host. Tried: "
        + ("; ".join(tried) if tried else "(no engines registered)")
        + ". The audio is retained and the pointer is on the row, so this is recoverable: "
          "install an engine and transcribe the same file again.")
