# Chats package

Owner: INFINITY-PLATFORM-admiral under SEAT-COMMON R45. This packet recovers the package from historical source `b2404ba2c1d4c3379be75fc1fe948fbd7a8e4844` onto integration `174ba99c5df4a8e3b98cac5be58b82215f399589`.

R29 requires this package and Attention's application import/registration to enter together in one combined admission. This packet does not modify `web/app.py`, mount the runtime, start a server, or alter shared shell templates. Importing `web.chats` adds no routes. `chats.register(app)` is the paired entry point and lazily imports Flask only when accessed.

## Behavior

| Module | Behavior |
| --- | --- |
| protocol | Local file messages, atomic replacement, terminator checks, incomplete-message reporting |
| registry | OS-minted agent IDs, registration records and mailboxes; transcript existence checked again on registration |
| roundtrip | Handler-based local task/report exchange, attribution by mailbox rather than sender header |
| wake | Windows filesystem notification or explicitly reported polling fallback |
| harness | Claude Code/Codex transcript-pointer metadata discovery for an explicitly supplied home |
| history | Streaming history windows and searches with record/turn counts |
| views and templates | Standalone history pages with plain links and native folds, no composer, send action or script |

The interface is Chats. Session/transcript backend vocabulary remains unchanged. The leading notice says that the page cannot send; interactive terminal release is a separate held decision. The page does not claim to be unmounted once an application renders it.

## Filesystem effects

This is a local filesystem package, not a database bridge. It does not call store or ingest transitions. The browser pages have no POST or send action, but they are **not strictly filesystem-read-only**.

| Call | Filesystem effect |
| --- | --- |
| Package import, build, register | Registers code/routes on the supplied app; no queue scaffold creation by these calls themselves |
| Index GET | Reads registrations and queue counts; `ensure_root` may create `<CHATS_ROOT>/register/_done` and `<CHATS_ROOT>/agents` |
| Agent GET | Reads a registration, transcript and queue counts, including processed-message directory counts |
| ensure_root, AgentClient construction | Creates queue scaffold directories when absent |
| send_registration | Creates scaffold, writes a generated registration message using `.part`, fsync and atomic replacement |
| accept_registrations | Creates per-agent mailboxes and their processed-message directories, writes agent.json and a reply, moves the registration message into its processed directory |
| hand_work / client report | Atomically writes generated local queue messages |
| mark_done | Moves the one already-read message into its mailbox's processed-message directory |

The default root is `~/.infinity-os/chats`; an explicit `CHATS_ROOT` or `base` argument selects it. The application owner must choose an approved root. Directory identity is routing within the operating system's filesystem permissions; this package adds no per-process filesystem permissions and makes no containment claim. It opens no network listener itself. A served view still depends on the application's deployment and access controls.

## Verification and limits

Use the restart packet's synthetic proof and report under `outputs/INFINITY-PLATFORM-admiral-chats/`. The prospective checklist states what is real and what is doubled. All transcript input is generated under a temporary fixture root. Archive/scaffold boundaries that would touch `_done` are simulated under the launch prohibition. Real archive/move end-to-end behavior remains untested by this packet. No user transcript, settings file, hook, provider client, or store is touched.

Historical helpers in `bin/` are retained as source evidence, not a recommended unattended run list. In particular, `hook_live_probe.sh`, `hook_windows_probe.sh`, and `shim_proof.py` can invoke old hook/store paths; `capture_coverage.py`, `capture_counterfactual.py`, `harness_registration_proof.py`, and `history_measure.py` can inspect caller-supplied installations or transcripts. Their historical figures are not current acceptance evidence. Some depend on files outside this package and are not runnable from the recovered package alone.

The restart proof exercises in-memory Flask fixtures only. It does not establish browser geometry, keyboard acceptance, a live mount, a capture hook round trip, or release clearance. Non-author review remains required. No new route registration hunk is authored in the application by this packet.

## Deliberate limits

Cross-host registration, a database indexing seam, automatic page updates, a search input, shared shell navigation, and a theme toggle are not implemented here. The existing query-parameter search covers renderable turns; record counts disclose the other record classes. A transcript verified at registration can later become inaccessible, so a display failure is not evidence that its conversation was empty. Rendering is HTML-escaped, but this package is not a transcript redaction layer. No real transcript is served during packet proof.
