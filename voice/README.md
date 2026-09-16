# voice: record, transcribe, land it as a normal prompt

**Lane V2, task 0377, 2026-08-18.**

The FEATURE is recording and transcription. **The DEFECT is the silence**, and the silence is
what this lane was built to close.

On 2026-08-17 the operator's daily monologue saved as **0 bytes**. He believed it had saved. It
had not, and nobody found out until a human opened the file by hand. He re-recorded and appended
a "part 2" that changed the shape of his whole day, which would otherwise have been lost. So the
intake path for this entire system depended on a manual save that failed silently.

## The one line the rest of this follows from

**The row is written before the audio exists.** `voice open` commits a `brain.voice_capture` row
with a `due_at` deadline *before the microphone is touched*.

When the artefact is the only evidence the attempt happened, an attempt that produces nothing
produces no evidence either, and there is nothing left to alarm on. **You cannot count an absence
you never wrote down.** From `open` onwards there are three outcomes and all three are visible:
the capture lands, or it fails and says at which stage, or it sits non-terminal past its deadline
and is `stuck`.

`stuck` is the one that matters. Failures raise something and a raise is the easy half. On
2026-08-18 this host lost its network for six hours; thirty swarm tasks stopped, nothing raised,
nothing paged, and a human found it. **Work that has begun and not finished throws no exception
anywhere.** A deadline written down before the work started is the only thing that catches it.

## Where a voice note lands, and why

**One raw intake row that a human sorts.** The whole transcript, uncut, through the **existing
`intake` verb** into `brain.objective` with `state='inbox'`. **The machine classifies nothing.**

There is no new landing verb and no second door. `intake` is the transition that has always put
an objective in the admiral's inbox (`engine/swarm_engine/transitions.py`); voice adds keyword
arguments to it. A voice note is an input **format**, not a subsystem.

The argument is the content. Monday's monologue holds durable objectives, the operator's own
tasks and things only he can decide, all at once. Landing typed rows would require the machine to
classify spoken English, and **a wrong classification is the same defect class as a fabricated
transcription: it will be believed later.** So the machine lands one row and the fan-out is a
human act through verbs that already exist: `accept` for an objective, `post` for a task, `ask`
for a decision only he can make.

Three consequences other lanes can rely on:

- A voice note is `brain.objective` with `intake_format='voice'` and `state='inbox'`. It is
  **never auto-accepted and never auto-posted** as a work item.
- The transcript is in `body`. **The audio is not in the store** -- `media_pointer`,
  `media_pointer_host` and `media_sha256` point at it, the posture `brain.transcript` states and
  the reason it states it.
- **`transcription_status` tells you whether `body` is trustworthy**: `ok` | `partial` | `null` |
  `unavailable`. Under `null`/`unavailable` the body is EMPTY and a CHECK refuses a non-empty
  one. **Never read a voice `body` without reading its status.**

## Never fabricate a transcription

Unintelligible audio is a **null**, not a guess. D3 measured the rule on `stated_goal`: *a
fabricated goal is worse than a null one, because it will be believed later.*

It is enforced at four layers, and the demonstration attacks all four
(`voice/tests/test-failure-demonstrations.py`, scene 3c):

| layer | what refuses |
|---|---|
| 1 | `transcribe.Transcription.__post_init__` -- every backend result passes through it |
| 2 | the `voice transcribed` verb |
| 3 | the `intake` verb, which is the door a voice note lands through |
| 4 | **the table's own CHECK**, reached with every verb bypassed |

Layer 4 is the load-bearing one. Layers 1-3 are code a later lane can edit.

The mirror half is enforced just as hard: a status that asserts there IS text, with an empty body
behind it, is the 2026-08-17 save arriving through the transcription door.

## What it does on this host, measured 2026-08-18

- **WSL2 has no audio device.** `/dev/snd` does not exist, there is no ffmpeg, sox or arecord, and
  `sudo` is closed to agents. So the recorder is a **Windows** process driven over interop:
  `voice/bin/record-windows.ps1`, winmm's `mciSendString` and nothing else, no third-party dll and
  no download. `waveInGetNumDevs()` on this host returns 1.
- **Transcription is `whisper.cpp` with a local `small.en` model, and `windows-sapi` behind it.**
  Both local, both free, both offline: **nothing leaves the machine at transcription time** under
  either. Measured on the real 2026-08-17 monologue, both engines in one session on the identical
  wav, scored by one scorer:

  | | WER | word errors / 871 | **key proper nouns** | wall clock |
  |---|---|---|---|---|
  | **whisper.cpp `small.en`** (default) | **0.0138** | **12** | **15 of 16** | 158.2 s |
  | windows-sapi (fallback) | 0.1481 | 129 | 8 of 16 | 12.2 s |

  **The proper-noun number is the one that decided this, not the WER.** SAPI's 86% word accuracy
  looked survivable and was not: it rendered a client's name as "a lively"
  and then "gladly", and dropped `TLDV`, `VSL`, `QC` and `starmynd.com` entirely. Whisper returns
  all of those. **It still misses that name** -- rendered as one consistent phonetic
  spelling a reader recognises, rather than two different real English words that read as facts.
- **`transcription_status` is not the same question as "can I trust a name in this".** So
  `accuracy_class` travels with the text: `names-reliable` means you may resolve a client or
  product from the body, `shape-only` means the shape of the day survives and the NAMES IN IT DO
  NOT. `voice engines` prints which engine takes the next capture and what its class is worth.
- **A missing model is `unavailable`, not an error** -- it falls through to `windows-sapi`, which
  needs no model and no build. The worst case of whisper being absent is the accuracy this system
  already shipped, never a capture that fails to land.
- A **silent room** produced 10 characters at mean confidence 0.0391 from SAPI, and the pipeline
  discarded them: below `CONFIDENCE_NULL_FLOOR` the words go and the status is `null`. **whisper
  fails differently and worse-looking:** it answers `[MUSIC PLAYING]` at confidence **0.7205**,
  far ABOVE that floor, because it is not unconfident -- it is confident there was music. A
  separate structural guard refuses non-speech annotation as a body. What was discarded is stated,
  never quietly dropped.
- **The hosted APIs are still not built and no code path for them exists.** They would send his
  business planning to a third party every morning and spend his money to do it. That is his
  decision and it is still open. See `docs/ENGINES.md`.

## Using it

```bash
export BRAIN_PG_DB=brain            # or a scratch database
voice/bin/voice record --seconds 600 --name monday-monologue-2026-08-18
voice/bin/voice ingest-file ~/memo.wav --name "a note I recorded on my phone"
voice/bin/voice health               # exits 4 when the line is not clean
voice/bin/voice ls
voice/bin/voice engines
```

Exit codes, because a cron branches on them and not on stdout:
`0` ok, `1` error, `2` nothing to do, **`3` a capture failed**, **`4` the health line is not
clean**.

`voice health` is the command to put in cron. It answers three questions, and the third is the
one no error handler asks: did anything fail, did anything land without words, and **was there a
capture at all today?** A day with zero captures on a machine whose operator records daily
produces no failed row to count, because nothing ever started.

## Running the demonstrations

```bash
export ENGINE_SCRATCH_DB=brain_t4_0377 BRAIN_PG_DB=brain_t4_0377
engine/bin/scratch-db.sh ensure
python3 voice/tests/test-failure-demonstrations.py
```

66 assertions, 0 failures on 2026-08-18. The run transcript is at
`outputs/2026-08-18-V2-voice-capture/FAILURE-DEMONSTRATIONS.txt`.

**A trap the suite itself fell into and now documents:** exit code 3 means *this capture failed*,
not *it failed at the stage you were testing*. The first upload scene read `rc=3` and counted a
pass for a stage it never exercised -- the copy had actually succeeded and the 3 came from a null
transcription. **Read `failure_stage` off the row, never the exit code.**

## Schema

`migrations/0024_objective_voice_intake.sql` (ledger 24) and
`migrations/0025_voice_capture.sql` (ledger 25). Both proven on a clone; applying them to live
`brain` is the operator's, per V00. See `docs/APPLY.md`.

## What this lane deliberately did NOT build

- **Transcript mining or search.** Out of scope twice over in the brief.
- **Any classification of what a note means.** That is the landing decision, above.
- **A paid or remote transcription path.** Raised as `q0402` with options and costs; the local
  path is built and running meanwhile. Nothing was spent and no byte left the machine.
- **Speaker diarisation, punctuation restoration, summarisation.** All would be plausible and all
  would be this lane inventing content on top of a transcript it already knows is 86% accurate.
