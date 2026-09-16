"""Where the verbs write. Two profiles, one set of transition functions.

`scratch` is D3's own wave-1 schema (`d3_scratch.ingest`), built before crosstalk slot 1
existed. `brain` is D1's migration 1 as applied (`brain.brain`, commit `e4228096`), published
2026-08-16T12:00Z. D3 never edits D1's migrations; this module only maps onto them.

**`brain` is the default since task 0228.** `BRAIN_PROFILE=scratch` opts back into D3's
wave-1 database, and nothing but a test should.

The default was `scratch` while D1's migration 1 was unpublished. It stayed `scratch` for six
hours after the hook was installed globally, and that is what the flip is for: the installed
hook entry in `~/.claude/settings.json` is a bare command with no `env` block, so every live
session inherited the process default and registered into `d3_scratch`, the one database
nobody queries. Measured: `brain.session` sat at 1,134 rows across the whole install window
while the hook log showed sessions starting and ending. Carrying the profile in the settings
file instead would put the fix in the operator's file rather than in this lane's code, where
a wrong default belongs.

## What the brain profile cannot carry, measured against migration 1

D1's `session` has 15 columns and `transcript` has 11. The scratch schema has 38 and 17. The
difference is not decoration — each column below was added because a measurement needed it —
so the drops are enumerated rather than silently absorbed. Posted to crosstalk slot 4 for D1
and the commander to rule on.

`session`, dropped on repoint:

    kind agent_id workflow_id                  the main/subagent distinction and the subagent's own id
    last_activity_at state                     THE IMPORTANT ONE, see below
    stated_goal_source stated_goal_tier        a goal with no provenance is the trap this lane measured
    model model_source entrypoint              no model column at all, so no per-model rollup
    permission_mode git_branch harness_version
    turns_user turns_assistant turns_source
    tokens_* cost_usd cost_source              no token columns, so cost stays underivable
    project_root_dir workdir_source
    parent_source registration_source

`transcript`, dropped on repoint:

    kind line_count bad_json_lines first_ts last_ts path_class windows_path verify_sha256

### The one that is a correctness problem, not a loss of detail

D1's `session` has `ended_at` and **no** `last_activity_at`. Measured over 1,129 backfilled
sessions: 1,129 have a last record timestamp and **0** have an observed end. A transcript's
last timestamp is a *lower bound* on the end, not an end; a crash and a clean exit look
identical in the file.

So on the brain profile, **`ended_at` is left NULL for every backfilled session.** Writing the
last-activity timestamp into `ended_at` would report a clean end time for 1,129 sessions
nobody ever saw finish. The fact is dropped rather than misfiled. Hook-registered sessions do
set `ended_at`, because those ends were actually witnessed.

`verified_ok boolean` has the same shape of problem one level down: it cannot hold `mismatch`
(the file changed -- corruption) and `missing` (the file is gone) apart, and the brain profile
used to write `false` for both. Task 0324 stopped doing that, without touching D1's column. A
gone file now gets `verified_at = now(), verified_ok = NULL`: checked, and neither a pass nor a
fail, because there was nothing to hash. `verified_at IS NULL` still means not-yet-verified, so
the three states are distinguishable on a nullable boolean. The MEANING of the absence -- did
it age out on schedule, or vanish inside the retention horizon -- lives one table over in
D3's own `ingest.transcript_absence`, because that is a fact about the harness's retention
behaviour and not a fact D1's schema ever claimed to carry. `unreadable` is the one verdict
still folded into `false`, and correctly so: a file that is there and will not open IS a failed
verification.
"""

from __future__ import annotations

import os

SCRATCH = "scratch"
BRAIN = "brain"


def profile() -> str:
    # The default is BRAIN. A caller that wants the scratch database says so; a caller that
    # says nothing -- a hook entry with no env, a cron line, an operator's shell -- lands
    # where the readers are looking.
    p = os.environ.get("BRAIN_PROFILE", BRAIN).strip().lower()
    if p not in (SCRATCH, BRAIN):
        raise ValueError(f"BRAIN_PROFILE must be {SCRATCH!r} or {BRAIN!r}, got {p!r}")
    return p


def is_brain() -> bool:
    return profile() == BRAIN


# --- D1 migration 1, brain.session -------------------------------------------------------
# Upsert on the text primary key `id`. session_key carries the same convention it does in the
# scratch schema: a main session's uuid, or '<parent_uuid>:<agent_id>' for a subagent. D1's
# `id` is text and `parent_session_id` self-references it, so the composite fits with no
# change to migration 1.
BRAIN_SESSION_UPSERT = """
INSERT INTO session (id, harness, agent, role, host, workdir, workdir_host, stated_goal,
                     parent_session_id, started_at, ended_at, actor_type, produced_by,
                     produced_by_producer, produced_by_ref, resolution_status)
VALUES (%(id)s, %(harness)s, %(agent)s, %(role)s, %(host)s, %(workdir)s, %(workdir_host)s,
        %(stated_goal)s, %(parent_session_id)s,
        COALESCE(%(started_at)s::timestamptz, now()), %(ended_at)s::timestamptz,
        %(actor_type)s::actor_type, %(produced_by)s, %(produced_by_producer)s,
        %(produced_by_ref)s, %(resolution_status)s)
ON CONFLICT (id) DO UPDATE SET
    harness           = COALESCE(NULLIF(EXCLUDED.harness,''), session.harness),
    agent             = COALESCE(NULLIF(EXCLUDED.agent,''), session.agent),
    role              = COALESCE(NULLIF(EXCLUDED.role,''), session.role),
    host              = COALESCE(NULLIF(EXCLUDED.host,''), session.host),
    workdir           = COALESCE(NULLIF(EXCLUDED.workdir,''), session.workdir),
    workdir_host      = COALESCE(NULLIF(EXCLUDED.workdir_host,''), session.workdir_host),
    -- first write wins, exactly as in the scratch profile
    stated_goal       = COALESCE(session.stated_goal, EXCLUDED.stated_goal),
    parent_session_id = COALESCE(EXCLUDED.parent_session_id, session.parent_session_id),
    started_at        = LEAST(session.started_at, EXCLUDED.started_at),
    ended_at          = COALESCE(EXCLUDED.ended_at, session.ended_at),
    actor_type        = COALESCE(EXCLUDED.actor_type, session.actor_type),
    -- The lineage group moves TOGETHER or the row lies. COALESCEing each column on its own
    -- would let a re-registration keep an old `resolved` status next to a newly-NULL producer,
    -- which is exactly the incoherent pair `session_lineage_coherent` refuses -- except the
    -- CHECK would fire on the merged row and blame the second write for the first one's data.
    -- So: a write that carries a status replaces all of them, and one that carries none keeps
    -- them. `resolution_status` is the discriminator because it is the only column of the
    -- group whose NULL means something ("no attempt") rather than "not applicable".
    --
    -- Migration 17 (task 0142) made it FOUR columns rather than three. `produced_by_producer`
    -- merges by exactly the same rule as `produced_by`, deliberately: the two are the split
    -- halves of one old column, and a rule that treated them differently would let a
    -- re-registration end up carrying both an entity id and a component name, which is the
    -- ambiguity the split exists to remove.
    produced_by          = CASE
                          WHEN EXCLUDED.resolution_status IS NOT NULL THEN EXCLUDED.produced_by
                          WHEN session.resolution_status  IS NOT NULL THEN session.produced_by
                          ELSE COALESCE(EXCLUDED.produced_by, session.produced_by) END,
    produced_by_producer = CASE
                          WHEN EXCLUDED.resolution_status IS NOT NULL
                            THEN EXCLUDED.produced_by_producer
                          WHEN session.resolution_status  IS NOT NULL
                            THEN session.produced_by_producer
                          ELSE COALESCE(EXCLUDED.produced_by_producer,
                                        session.produced_by_producer) END,
    produced_by_ref   = CASE
                          WHEN EXCLUDED.resolution_status IS NOT NULL
                            THEN EXCLUDED.produced_by_ref
                          ELSE session.produced_by_ref END,
    resolution_status = COALESCE(EXCLUDED.resolution_status, session.resolution_status)
-- `actor_type` is RETURNED so the `session.started` envelope can carry what this row now HOLDS
-- rather than what the caller passed (task 0300). The two differ whenever the merge above
-- chooses the stored value over EXCLUDED, and a second independent statement of the same fact
-- is what let 150 events say `ai` about sessions this table says are `hybrid`.
RETURNING id, actor_type::text AS actor_type, (xmax = 0) AS inserted
"""

BRAIN_SESSION_END = """
UPDATE session SET ended_at = COALESCE(%(ended_at)s::timestamptz, ended_at)
WHERE id = %(id)s
-- `actor_type` rides the RETURNING for the same reason it does on the upsert above (task 0300):
-- the `session.ended` envelope carries the row's own answer. `session end` never writes this
-- column, so the value here is whatever `session register` established, which is the point.
RETURNING id, ended_at, actor_type::text AS actor_type
"""

BRAIN_TRANSCRIPT_UPSERT = """
-- `produced_by_producer` and not `produced_by` since migration 17 (task 0142): a transcript row
-- names the component that indexed it and makes no claim about the brain, so the lineage triple
-- stays entirely NULL and `produced_by` is left free to mean "an entity id" everywhere.
INSERT INTO transcript (session_id, pointer, pointer_host, sha256, bytes, harness,
                        produced_by_producer)
VALUES (%(session_id)s, %(pointer)s, %(pointer_host)s, %(sha256)s, %(bytes)s, %(harness)s,
        %(produced_by_producer)s)
ON CONFLICT (pointer, pointer_host) DO UPDATE SET
    session_id  = COALESCE(EXCLUDED.session_id, transcript.session_id),
    sha256      = EXCLUDED.sha256,
    bytes       = EXCLUDED.bytes,
    indexed_at  = now(),
    verified_at = NULL,
    verified_ok = NULL
RETURNING id AS transcript_id, sha256, bytes
"""

# `session_id AS session_key` since task 0324: when verify finds the file gone it books an
# `ingest.transcript_absence` row, and that row keeps the key so the loss is still attributable
# after the file is unreadable. Selecting it here rather than re-querying by pointer keeps the
# absence row inside verify's own transaction with no second lookup.
BRAIN_TRANSCRIPT_SELECT_FOR_VERIFY = """
SELECT id AS transcript_id, pointer AS path, sha256, bytes, session_id AS session_key
FROM transcript
WHERE pointer_host = %(host)s ORDER BY verified_at NULLS FIRST, id LIMIT %(limit)s
"""

# `transcript verify --id` and `--path`. Task 0228: these two selects named the scratch columns
# unconditionally, so on the brain profile they raised UndefinedColumn while the no-argument
# form above worked. Harmless while `scratch` was the default and a broken command the moment
# it stopped being.
BRAIN_TRANSCRIPT_SELECT_BY_ID = """
SELECT id AS transcript_id, pointer AS path, sha256, bytes, session_id AS session_key
FROM transcript WHERE id = %(id)s
"""

BRAIN_TRANSCRIPT_SELECT_BY_POINTER = """
SELECT id AS transcript_id, pointer AS path, sha256, bytes, session_id AS session_key
FROM transcript
WHERE pointer_host = %(host)s AND pointer = %(pointer)s
"""

# `ok` is NULL for a gone file since task 0324, not false. verified_at IS NOT NULL with
# verified_ok IS NULL is a third state and says exactly what happened: the row was checked,
# and the answer was neither pass nor fail because there was no file to hash. `verified_ok =
# NULL` with `verified_at IS NULL` remains "not yet verified" -- the two are distinguishable
# because the upsert clears both together and this statement never does.
BRAIN_TRANSCRIPT_MARK_VERIFIED = """
UPDATE transcript SET verified_at = now(), verified_ok = %(ok)s WHERE id = %(id)s
"""

# The session timestamps a gone pointer is aged against (task 0324). Both profiles return the
# same three names so the caller has one rule; `last_activity_at` is selected as NULL here
# because this profile has no such column -- see the enumerated losses above. The column that
# actually supplied the answer is written onto the absence row rather than left to be guessed.
BRAIN_SESSION_AGE_BASIS = """
SELECT ended_at, NULL::timestamptz AS last_activity_at, started_at
FROM session WHERE id = %(key)s
"""

SCRATCH_SESSION_AGE_BASIS = """
SELECT ended_at, last_activity_at, started_at FROM session WHERE session_key = %(key)s
"""
