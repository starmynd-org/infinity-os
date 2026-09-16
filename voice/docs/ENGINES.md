# Transcription engines: what is running, what it costs, and what is the operator's to decide

**Raised as `q0402` on 2026-08-18 and ANSWERED the same day: *attempt B, timeboxed, keep A as
the fallback.* Nothing was spent and no byte left this machine.**

The brief's hard limit: *anything that spends the operator's money or sends outside the machine
is raised, not chosen. If transcription needs a paid or remote API, RAISE IT with options and
costs and build the local path meanwhile.* That is what happened.

**A and B are both built, running, and measured. B is the default; A is the fallback and was
not removed.** C and D are described and **deliberately not built**: they send the operator's
daily monologue to a third party and spend his money on a recurring basis, and **that decision
is his and is still open.** No code path for them exists, one config key away or otherwise.

| | engine | state | WER | proper nouns | leaves the machine? |
|---|---|---|---|---|---|
| **B** | whisper.cpp `small.en` | **default** | **0.0138** | **15 of 16** | no |
| **A** | windows-sapi | **fallback** | 0.1481 | 8 of 16 | no |
| C | hosted API | **not built** | ~0.02-0.05 (cited) | unmeasured | **YES, every morning** |
| D | see below | **not built** | -- | -- | -- |

## A. windows-sapi -- built, running, measured, **AND KEPT AS THE FALLBACK**

**It is not superseded and it was not removed.** It needs no model file and no build, so it is
what answers on a host where the model was never downloaded or has gone missing. `q0402` was
answered *keep A as the fallback*, and this is that A. Re-measured by 0405 on the same sample:
**WER 0.1481, 129 errors in 871 words, 8 of 16 key proper nouns.** (V2 measured 0.1385 and 10 of
16 on an earlier run of the same nondeterministic engine with a slightly different tokenizer;
both readings say the same thing about what it loses.)

`System.Speech.Recognition` with `MS-1033-80-DESK` (en-US), the one recogniser installed on this
Windows host. **Local, free, offline, no model download, no third-party dll.**

Measured 2026-08-18 on the operator's real 2026-08-17 monologue, spoken to a 16 kHz wav and put
through the same pipeline a live recording uses:

| | |
|---|---|
| audio | 316.44 s (5m16s), 10,126,126 bytes |
| transcription wall clock | **13.9 s** -- about 23x faster than real time |
| output | 4,228 characters, 70 segments, `status=ok` |
| mean confidence | 0.7905 |
| **word accuracy** | **86.15%** (117 errors in 845 reference words, WER 0.1385) |
| key proper nouns recovered | **10 of 16** |

**The headline number is the flattering one.** What it loses is the tokens a downstream agent
would key on:

| said | transcribed |
|---|---|
| a client's name | "a lively", then "gladly" two sentences later |
| Infinity OS | "Infiniti OS" |
| Matt | "mad" |
| TLDV, VSL, QC, starmynd.com | gone |

So the local engine gets the **shape** of a day right and loses the **names** in it. It is
genuinely useful for "what was Monday about" and genuinely unsafe as the input to anything that
resolves an entity.

Second measurement, and it is the one that shaped the code: eight seconds of a **silent room**
produced 11 characters at mean confidence **0.0423**, and again 10 characters at **0.0391**. The
engine will invent words out of noise. `CONFIDENCE_NULL_FLOOR` (0.20) discards them and reports
`null` with an empty body; the discarded character count and confidence are recorded, never
quietly dropped.

## B. whisper.cpp with a downloaded model -- **BUILT, RUNNING, MEASURED, AND NOW THE DEFAULT**

**Task 0405, 2026-08-18, answering `q0402`: ATTEMPT B, TIMEBOXED, KEEP A AS THE FALLBACK.**
Timebox named up front: 60 minutes to build and transcribe one real sample on this box, clock
started 08:45:41Z. It built and transcribed inside **18 minutes**. Nothing was spent and no byte
left the machine.

### The measurement, both engines, one scorer

Both engines were run **in the same session on the identical wav** and scored by the **same**
scorer, so the delta below is a delta and not an artifact of two different rulers. Sample:
`/mnt/c/Users/you/VoiceCaptures/20260818/vc-20260818T074609Z-b927fd.wav`, 316.44 s,
sha256 `39ffc564...f8d42`, verified against the hash recorded on the capture row.

| | windows-sapi | **whisper.cpp `small.en`** |
|---|---|---|
| WER | 0.1481 | **0.0138** |
| word errors / 871 | 129 | **12** |
| **key proper nouns** | **8 of 16** | **15 of 16** |
| wall clock (316 s audio) | 12.2 s | 158.2 s |
| mean confidence | 0.9235 | 0.9722 |

**10.75x fewer word errors, and nearly double the proper-noun recall.** Everything `q0402` was
raised about comes back: `TLDV`, `VSL`, `QC`, `starmynd.com`, `Infinity OS`, `Matt`, `AI`.

**The one name it still loses is the client's**, rendered as one consistent misspelling. That is a weaker failure than
SAPI's and the difference matters: SAPI produced *"a lively"* and then *"gladly"* -- two
different real English words that read as facts and cannot be found by searching for the client.
Whisper produces one consistent phonetic spelling in all three places, which a reader recognises
on sight and one substitution fixes. **It is still a miss and it is counted as one.**

The trade is wall clock: whisper is ~13x slower than SAPI and still ~2x faster than real time,
so a 5-minute monologue costs ~2.6 minutes of CPU. Correct for a batch intake path, wrong for
anything interactive. Nothing in this package is interactive.

### Model provenance, verified rather than asserted

| | |
|---|---|
| file | `ggml-small.en.bin` |
| size | **487,614,201 bytes**, matched to the byte |
| sha256 | `c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d` |
| source | `https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin` |
| verified | `sha256sum` after download **matches** the HuggingFace LFS oid for that path |
| lives at | `/home/you/.whisper-models/ggml-small.en.bin` |

Downloaded as `.part` and renamed only after both the size and the hash checked out, so a
truncated download can never be mistaken for a model. The build is whisper.cpp `1.9.2`
(ggml 0.20.0, commit `1fe009c`), compiled on this ARM64 box; binary and its four shared objects
are copied to `/home/you/.whisper-tools/whisper/`, **not** left in the `/tmp` build tree,
because `/tmp` does not survive a reboot and a default pointing there would silently downgrade
every future monologue back to `shape-only`.

### Nothing leaves the machine at transcription time

This is the entire reason B is acceptable where C is not, so it is **demonstrated, not claimed.**
The model is a **one-time** download; transcription is a local process reading a local file.
The measurement above was produced inside `unshare -rn` -- a network namespace with no interface
but a down `lo`, in which `curl https://huggingface.co` returns rc=6, *could not resolve host*.
Demonstration 6c in `voice/tests/test-failure-demonstrations.py` re-proves it on every run.

### The hazard it introduced, which the old floors could not catch

whisper does **not** invent sentences out of noise the way SAPI did. It **labels** the noise, and
it is **confident** about the label. On the same two silent-room captures that made
`CONFIDENCE_NULL_FLOOR` exist:

| capture | whisper said | mean token probability |
|---|---|---|
| `vc-20260818T073842Z-74c591` | `[ Background noise ]` | **0.5798** |
| `vc-20260818T073948Z-44bb25` | `[MUSIC PLAYING]` | **0.7205** |
| `vc-20260818T074020Z-fecd5f` (real speech) | word perfect | 0.9780 |

**Both annotations sit far above the 0.20 null floor** and the second is nearly at the 0.75 `ok`
floor. The floor cannot see them and was never built to: the model is not unconfident, it is
confident there was music. **Low confidence and non-speech are different failures and only one
of them is a number.** Stored unguarded, `[MUSIC PLAYING]` becomes a sentence the operator
supposedly spoke -- the same defect class as a fabricated transcription, and refused the same
way by `WHISPER_ANNOTATION_RE`: annotations are stripped from the body, and a body that is
nothing but annotation is `null` with an empty body and a detail saying what was refused.

### Do not add `-nt`

Measured, same binary, same model, same file, that flag the only difference:

| | without `-nt` | with `-nt` |
|---|---|---|
| WER | **0.0138** | 0.1102 |
| proper nouns | **15 of 16** | 14 of 16 |
| segments | 77 | 11 |
| chars | 4389 | 3968 -- **79 words silently gone** |

Whisper generates timestamp tokens as part of its output sequence, so suppressing them changes
the decode path. The dropped clause was **not** marked missing anywhere: confidence stayed 0.947
and status stayed `ok`. A confident, clean-looking, 9%-shorter transcript is exactly the failure
this lane exists to make impossible.

## C. A hosted API -- NOT built, and it is the part that is not a technical decision

OpenAI's Whisper API is 0.006 USD per audio minute. A 5-15 minute monologue is **0.03-0.09 USD a
day, roughly 1-3 USD a month.** WER on clean English is typically 2-5%. Deepgram and AssemblyAI
sit in the same posture at a similar price.

**The cost is trivial and the cost is not the question.** The daily monologue is the operator's
business planning, his clients by name, his finances and his own thinking out loud. Option C
sends that recording to a third party every morning. That is his call, and specifically not one
an agent makes on his behalf for a 5-point accuracy gain.

## What holds whichever way he answers

**The audio is retained and hashed, so this decision is not urgent and is never lost.**
`media_pointer` + `media_pointer_host` + `media_sha256` on every capture and every voice
objective. Every note landed under option A can be transcribed again, from the same file, by
whatever engine he picks -- and the hash proves it is the same file.

Adding an engine is one class with two methods (`available()` and `transcribe()`) in
`voice/voice_capture/transcribe.py` and one entry in `ENGINES`. Nothing else in the package
changes, and the never-fabricate guard applies to any backend because every result passes through
`Transcription.__post_init__`.

**`q0402` WAS ANSWERED: attempt B, timeboxed, keep A as the fallback. That is what shipped.** The
engine order is now `[whisper.cpp, windows-sapi]` and `voice engines` prints which one takes the
next capture and what its accuracy class is worth. The paragraph below is kept as the record of
what the fallback costs, because it is what the operator gets if the model ever goes missing:

**If the model is missing:** the lane falls back to A. Every monologue lands, most of them as
`partial`, with about one word in seven wrong and the proper nouns unreliable, and the audio
sitting next to each one waiting for a better engine. Nothing is spent, nothing leaves the
machine, and nothing is lost by waiting.
