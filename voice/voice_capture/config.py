"""Where the audio goes, how long a stage may take, and which host said so.

Every value here is overridable from the environment, and every default is stated with the
reason it is that value rather than another one. A threshold nobody can find is a threshold
nobody will ever raise, and an unraisable threshold gets worked around instead.
"""

from __future__ import annotations

import os
import platform
import re
from pathlib import Path

#: A capture id: `vc-<UTC compact>-<6 hex>`. Chosen before anything is recorded so that the wav
#: file, the store row, the log line and the objective name all carry the same string. Narrow
#: because it becomes a filename.
CAPTURE_ID_RE = re.compile(r"^vc-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$")

#: Where the Windows recorder writes. VOLATILE by design: it is a temp directory, and the fact
#: that it is volatile is exactly why `retain` is a stage with its own failure rather than a
#: detail of recording. WSL2 has no audio device at all on this host (`/dev/snd` does not exist,
#: measured 2026-08-18), so the recorder is a Windows process and this path has to be reachable
#: from both sides of the boundary.
SCRATCH_DIR = Path(os.environ.get(
    "VOICE_SCRATCH_DIR", "/mnt/c/Users/you/AppData/Local/Temp/voice-capture"))

#: Where the audio is RETAINED, and it is on the Windows side on purpose. The alternative,
#: WSL's ext4 home, is faster and is destroyed by `wsl --unregister`, which is a normal thing to
#: do to a distro and not a normal thing to do to the operator's recordings. V00 records that
#: this system's durability tier is `ledger`, not `mirrored` -- nothing is pushed anywhere -- so
#: the audio inherits whatever backs the Windows profile and nothing better. V6 is the lane that
#: changes that, and when it does, this is one line.
RETAIN_DIR = Path(os.environ.get("VOICE_RETAIN_DIR", "/mnt/c/Users/you/VoiceCaptures"))

#: PCM 16 kHz mono 16-bit. Not a preference: it is what the Windows SAPI recogniser on this host
#: wants, and resampling with no ffmpeg installed and no route to install one is not available.
SAMPLE_RATE = int(os.environ.get("VOICE_SAMPLE_RATE", "16000"))
CHANNELS = 1
BITS_PER_SAMPLE = 16
MEDIA_KIND = "audio/wav; codec=pcm_s16le"

#: The RIFF header alone is 44 bytes and a file of exactly that size is the 0-byte save with a
#: header on it. A quarter second of audio at the settings above is 8000 bytes; anything under
#: that is not a recording whatever the recorder's exit code said.
MIN_WAV_BYTES = int(os.environ.get("VOICE_MIN_WAV_BYTES", "8044"))
WAV_HEADER_BYTES = 44

#: How much slack a stage gets past the seconds it asked for, before the capture is STUCK. The
#: microphone open, the file save and the move are each sub-second on this host; 120 seconds is
#: two orders of magnitude of headroom and still short enough that a wedged capture is found on
#: the same day it wedged rather than in a weekly review.
STAGE_SLACK_SECONDS = int(os.environ.get("VOICE_STAGE_SLACK_SECONDS", "120"))

#: SAPI on a long recording is slow: it runs faster than real time but not by much. Budgeted as
#: a multiple of the audio's own duration plus a floor, so a 20-minute monologue is not judged
#: stuck at the same deadline as a 20-second note.
TRANSCRIBE_TIMEOUT_FLOOR_S = int(os.environ.get("VOICE_TRANSCRIBE_FLOOR_S", "180"))
TRANSCRIBE_TIMEOUT_RATIO = float(os.environ.get("VOICE_TRANSCRIBE_RATIO", "1.5"))

#: Below this mean confidence the transcript is reported `partial` rather than `ok`. The local
#: Windows recogniser (`MS-1033-80-DESK`) scored 0.646 on clean synthesised speech on this host
#: on 2026-08-18 -- measured, not quoted -- so `ok` is a claim this engine will rarely earn, and
#: that is the honest reading rather than a threshold tuned until it passes.
CONFIDENCE_OK_FLOOR = float(os.environ.get("VOICE_CONFIDENCE_OK_FLOOR", "0.75"))

#: BELOW THIS, THE WORDS ARE DISCARDED AND THE STATUS IS `null`. Not a tuning knob and not a
#: tolerance: it makes the check STRICTER, and it exists because the first real capture found
#: the hazard rather than because a threshold looked untidy.
#:
#: Measured 2026-08-18, capture vc-20260818T073842Z-74c591: eight seconds of a SILENT room, and
#: the Windows recogniser returned 11 characters at mean confidence 0.0423. An engine that does
#: not stand behind its own output at four percent is guessing at noise, and a guess stored as a
#: transcript is worse than a null one because it will be believed later -- D3 measured that rule
#: on `stated_goal` and it is the hard limit this lane was given.
#:
#: 0.20 rather than something nearer 0.0423, so the floor is a decision about what "the engine
#: stands behind this" means rather than a number drawn around one observation. What is discarded
#: is never hidden: the character count and the confidence go into the capture's detail, and the
#: audio is retained and hashed, so a better engine can be run over the same file later.
CONFIDENCE_NULL_FLOOR = float(os.environ.get("VOICE_CONFIDENCE_NULL_FLOOR", "0.20"))

#: The operator records daily. `voice health --expect-daily` reads this to answer the question
#: that actually failed on 2026-08-17: was there a capture at all today?
EXPECT_DAILY = os.environ.get("VOICE_EXPECT_DAILY", "1") != "0"


def host() -> str:
    """This host's name, for `media_pointer_host`.

    `platform.node()` and not a config value, unlike `swarm post --host`: that one is relayed by
    a CLI which may be running somewhere else, so `uname()` would be the wrong answer there. This
    one is stamped by the process that has the file open, and the process that has the file open
    IS on the host the path is absolute on.
    """
    return platform.node() or "unknown"


def windows_path(p: Path) -> str:
    """`/mnt/c/x/y` -> `C:\\x\\y`, because the recorder is a Windows process.

    Refuses anything it cannot convert rather than guessing. A path this cannot express is a path
    the Windows recorder cannot write to, and finding that out here beats finding it out as an
    MCI error code with no filename in it.
    """
    s = str(p)
    if re.match(r"^[A-Za-z]:[\\/]", s):
        return s.replace("/", "\\")
    m = re.match(r"^/mnt/([a-z])/(.*)$", s)
    if not m:
        raise ValueError(
            f"{s!r} is not reachable from Windows. The recorder runs as a Windows process "
            f"because WSL2 on this host has no audio device at all, so every path it touches "
            f"has to live under /mnt/<drive>. Set VOICE_SCRATCH_DIR to one.")
    return f"{m.group(1).upper()}:\\" + m.group(2).replace("/", "\\")


# --------------------------------------------------------------------------------------------
# whisper.cpp, the local model engine. Added 2026-08-18 by task 0405, answering `q0402` with
# ATTEMPT B, TIMEBOXED, KEEP A AS THE FALLBACK.
# --------------------------------------------------------------------------------------------

#: The `whisper-cli` launcher. NOT on PATH and deliberately not installed system-wide: this box
#: has no `sudo` for agents, so the build lives in the operator's own tree and the path is a
#: config value rather than an assumption. Missing or non-executable => the engine reports
#: `unavailable` and `windows-sapi` takes the capture. That fallback is the whole reason B was
#: allowed to be attempted at all, so it is a default and never a requirement.
#:
#: NOT the build tree. whisper.cpp was compiled at `/tmp/whisper.cpp`, and `/tmp` does not
#: survive a reboot. A default pointing there would work today, vanish on the next boot, and
#: quietly put every future monologue back on the engine that cannot spell the operator's
#: client's name -- a downgrade nobody chose and nobody would be told about. The binary and its
#: four shared objects are therefore COPIED under the operator's home, and this is the launcher
#: that sets `LD_LIBRARY_PATH` to the copies rather than to the build tree.
WHISPER_BIN = Path(os.environ.get(
    "VOICE_WHISPER_BIN", "/home/you/.whisper-tools/whisper/bin/whisper-cli.sh"))

#: The ggml model. `small.en` measured 2026-08-18 on this host, provenance recorded in
#: `voice/docs/ENGINES.md`: 487,614,201 bytes, sha256
#: c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d, from the canonical
#: ggerganov/whisper.cpp HuggingFace repo. A MISSING MODEL IS NOT AN ERROR, it is `unavailable`,
#: which is the operator-facing act "download the model" rather than "re-record the room".
WHISPER_MODEL = Path(os.environ.get(
    "VOICE_WHISPER_MODEL", "/home/you/.whisper-models/ggml-small.en.bin"))

#: Measured on this host: 12 threads transcribed 316.44 s of audio in 158.2 s, about 2x faster
#: than real time. SAPI did the same file in 12.2 s. Whisper is ~13x slower and 10.75x more
#: accurate, which is the right trade for a batch intake path and the wrong one for anything
#: interactive. Nothing in this package is interactive.
WHISPER_THREADS = int(os.environ.get("VOICE_WHISPER_THREADS", "12"))

#: NON-SPEECH ANNOTATIONS, AND WHY THIS EXISTS AS A SEPARATE GUARD FROM THE CONFIDENCE FLOOR.
#:
#: Measured 2026-08-18 on the two silent-room captures that made `CONFIDENCE_NULL_FLOOR` exist.
#: Whisper does not invent sentences out of noise the way SAPI did; it labels the noise, and it
#: is CONFIDENT about the label. `vc-20260818T073842Z-74c591` returned `[ Background noise ]` at
#: mean token probability **0.5798** and `vc-20260818T073948Z-44bb25` returned `[MUSIC PLAYING]`
#: at **0.7205**. Both sit far ABOVE the 0.20 null floor, so the floor does not catch them and
#: was never built to: low confidence and non-speech are two different failures and only one of
#: them is a number.
#:
#: Stored unguarded, `[MUSIC PLAYING]` becomes a sentence the operator supposedly spoke. That is
#: the same defect class as a fabricated transcription and it is refused the same way. Bracketed
#: spans are removed from the body, and a body that is NOTHING BUT annotation is `null` with an
#: empty body -- never a silent pass-through, and never a reason to discard real words that
#: happened to share a segment with one.
WHISPER_ANNOTATION_RE = re.compile(r"[\[(][^\]\)]{0,80}[\])]")

#: How long `whisper-cli` may run per second of audio, plus `TRANSCRIBE_TIMEOUT_FLOOR_S`. The
#: measured ratio on this host is 0.50 (158.2 s for 316.44 s); 3.0 is 6x headroom, because the
#: number that matters is the one on a loaded box and not the one on an idle one.
WHISPER_TIMEOUT_RATIO = float(os.environ.get("VOICE_WHISPER_RATIO", "3.0"))
