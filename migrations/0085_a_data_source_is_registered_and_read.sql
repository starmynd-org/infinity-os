-- migration 85: A DATA SOURCE IS REGISTERED AND READ, and goals/KPIs hang off the ones it names.
--
-- LEDGER VERSION 85 (renumbered from 80 on 2026-09-16 by W5-S7 on ALPHA-COMMANDER-3's ruling; see
-- WHY 85 below). As first written: `engine/bin/scratch-db.sh ledger` reported "74 versions read, 1..77, HOLES AT
-- 54 55 60. NEXT VERSION IS 78 -- take max(version) + 1", measured against this worktree at
-- fece8c3 on 2026-09-16. Not a hole: 54, 55 and 60 are left alone per that script's own warning
-- ("NEVER TAKE A HOLE").
--
-- WHY 80 AND NOT 78 (renumbered 2026-09-16 on ALPHA-COMMANDER-2's go). That script sees one tree
-- only. Across every origin branch, 78 is already recorded by an unmerged migration,
-- `origin/mv/platform-objectives` queue/schema/0018_objectives_reach_attention.sql, and 79 by
-- `origin/mv/platform-tenancy-schema` migrations/0079_human_login_ceiling_removed.sql. 80 appeared
-- on no origin tip and in no origin history when this file was renumbered. Every ledger file
-- writes ON CONFLICT (version) DO NOTHING, so a colliding version would apply nothing and still
-- report success. Before applying, re-check across all branches, not only this tree.
--
-- WHY 85 AND NOT 80 (renumbered 2026-09-16 by W5-S7 on ALPHA-COMMANDER-3's ruling). "Every origin
-- branch" was still not every branch. Every version from 79 to 84 is claimed, most of them by
-- LOCAL-ONLY commits on no remote: 79 origin/mv/platform-tenancy-schema
-- `0079_human_login_ceiling_removed.sql`; 80 local mv/platform-tenancy-identity
-- `0080_only_the_instance_owner_changes_identity_mappings.sql` (cf68110); 81 local
-- mv/platform-tenancy-workspace `0081_workflow_rows_name_their_workspace.sql` (013c963); 82 and 83
-- local infa/mos-1 and infa/mos-2 `0082_a_declared_repo_is_indexed_as_an_os_a_repo_or_a_problem.sql`
-- and `0083_rows_the_tabs_show_name_their_os.sql`; 84 local infa/mos-3
-- `0084_a_sync_leaves_a_receipt_and_the_store_names_its_floor.sql`. A coordinator ruling in
-- the product factory repository (IPR-20, PROVISIONAL pending Andrew) had reserved 79 to 81 and named 82 as
-- next; it had not seen the infa/mos branches. 85 was the first version with no claim on master,
-- any remote branch, any local branch of the shared checkout or the scratch clones, or any
-- worktree's files on disk (W5-S7 census, 2026-09-16). The check that finds a claim is all of
-- those, and both halves of it: a `VALUES (<v>,` ledger row AND a file named 00<v>_, because
-- neither half alone found every claim.
--
-- NOT RUN ON BRAIN. Written in worktree `w4/v002-data` off `origin/master` at `fece8c3` by
-- W4-V002-DATA (Commander B). Proven on a per-pid SCRATCH database only (see
-- `outputs/2026-09-16-w4-v002-data/SCRATCH-PROOF.md`), never against `brain`, per this repo's
-- `CLAUDE.md`: "Do not apply migrations to the live store. Write them, prove them on scratch,
-- hand over the apply order." Applying this to `brain` is Andrew's / ALPHA-COMMANDER-2's call.
--
-- ================================================================= changes after the first cut
--
-- ALPHA-COMMANDER-2 approved D3 with three named changes to the file this census/contract cycle
-- first produced (then numbered 78, then renumbered 80, then 85 -- see above):
--
--   1. P0, credential refusal. brain.credential_shape() and two CHECK constraints refuse a
--      `location` or `description` that looks like a URL user:password@, a password=/sslpassword/
--      api_key= assignment, an sk-style key, a JWT, or a PEM private-key header. Proven by a real
--      mutation: the check was removed, the file's hash was shown to move, the self-check below
--      failed BY NAME against that mutant, the file was restored and hashed again to match the
--      original, and the self-check was re-run green. Full transcript in the SCRATCH-PROOF output.
--   2. Proof on scratch, this DO block. Written imitating migration 0072's refusal-and-control
--      shape: named refusals that must fire, named controls that must pass, a reconciled count,
--      fixture rows removed at the end.
--   3. P2, amended_by/amended_at. brain.data_source_register()'s UPDATE branch (a second
--      registration of the same name) now stamps amended_by/amended_at from
--      brain.current_human(), never from a caller. The INSERT branch (first registration) leaves
--      both NULL. Covered by controls 6 and 7 below.
--
-- Everything else in this file is unchanged from the version ALPHA-COMMANDER-2 read and approved.
--
-- ================================================================= what this answers
--
-- `W4-V002-DATA-CENSUS.md` (same author, same day) found NO existing table, view or function
-- named for a registered data source, a goal or a KPI anywhere in `migrations/`, `budget/schema/`
-- or `queue/schema/` (57 CREATE TABLE statements read, none of them; ~41 CREATE VIEW statements
-- read, none of them). D-9 (Postgres on the VPS is the source of truth, not Rowy) and D-10 (goals
-- and KPIs are children of the data section) are both closed decisions this file implements
-- rather than reopens. This is new schema, not a reuse of anything found.
--
-- ================================================================= the narrow waist, applied
--
-- This repo's own convention, read in migration 0024's comment: "the narrow waist (D00/V00) says
-- one transition function per state change, exposed as a verb, called by every surface." Applied
-- here: every write goes through exactly one of the four functions below (register, touch, write,
-- upsert), never a bare INSERT from a route handler. `web/app.py`, a future CLI verb, and any
-- connector that learns to report its own row count all call the SAME function, so there is one
-- place that decides what a valid data source, goal or KPI looks like.
--
-- ================================================================= the shape
--
-- brain.data_source     one row per source the data page lists: name, kind (live / database /
--                       sheet, per the approved mockup's three tabs), where it lives, and when it
--                       was last read. `stale_after` is per-row because a weekly sheet and a live
--                       feed do not share one definition of "old" (D2's item (e), ageing).
-- brain.goal            one row per goal: target, current reading, why, done-when, owner.
-- brain.goal_source     goal <-> data_source, many-to-many: what a goal is "measured from".
-- brain.kpi             one row per KPI: target, current reading, and the ONE source it reads
--                       (per the approved mockup, a KPI names a single source, not a set).
-- brain.data_source_health   a view, not a table: turns `last_seen_at` and `stale_after` into
--                       fresh / stale / never, which is what makes a dead source visible rather
--                       than a row that silently stops updating and looks the same as one that
--                       is current.
--
-- "Reported in" (the report chips on the Goals mockup) is DELIBERATELY NOT a foreign key here.
-- Commander C's reporting section is not built and its report/session entity does not exist in
-- this repo at fece8c3 (`W4-V002-DATA-CENSUS.md` control: zero hits for `starmynd-reporting` in
-- this repository). `brain.goal_reported_in` stores a plain label for now; it is named so a real
-- foreign key can replace it in place when Commander C's report entity lands, without renaming
-- the column every reader already uses.
--
-- ================================================================= rollback, not executed
--
--     BEGIN;
--       DROP VIEW IF EXISTS brain.data_source_health;
--       DROP FUNCTION IF EXISTS brain.kpi_upsert(text, text, text, numeric, numeric, text, text);
--       DROP FUNCTION IF EXISTS brain.goal_write(text, text, text, text, text, text, text);
--       DROP FUNCTION IF EXISTS brain.data_source_touch(text, bigint);
--       DROP FUNCTION IF EXISTS brain.data_source_register(text, text, text, text, interval);
--       DROP TABLE IF EXISTS brain.goal_reported_in;
--       DROP TABLE IF EXISTS brain.goal_source;
--       DROP TABLE IF EXISTS brain.kpi;
--       DROP TABLE IF EXISTS brain.goal;
--       DROP TABLE IF EXISTS brain.data_source;
--       DROP FUNCTION IF EXISTS brain.credential_shape(text);
--       DELETE FROM brain.schema_migration WHERE version = 85;
--     COMMIT;
--
-- Two-way in DDL. One-way in data, same as every other migration in this ledger that lands a new
-- table: whatever has been registered by the time this is rolled back is lost, because nothing
-- else in the store remembers it. On the day this lands there is nothing to lose.

\set ON_ERROR_STOP on

BEGIN;

-- ---------------------------------------------------------------- P0: what a credential looks
-- like, named so a refusal can name it back

CREATE OR REPLACE FUNCTION brain.credential_shape(p_text text) RETURNS text
  LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN p_text IS NULL THEN NULL
    WHEN p_text ~ '://[^/@[:space:]]+:[^/@[:space:]]+@' THEN 'a URL user:password@ shape'
    WHEN p_text ~* 'sslpassword' THEN 'an sslpassword shape'
    WHEN p_text ~* 'password[[:space:]]*=' THEN 'a password= shape'
    WHEN p_text ~* 'api[_-]?key[[:space:]]*=' THEN 'an api_key= shape'
    WHEN p_text ~ '\msk[_-][A-Za-z0-9_-]{16,}\M' THEN 'an sk- style API key shape'
    WHEN p_text ~ 'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+' THEN 'a JWT shape'
    WHEN p_text ~ '-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----' THEN 'a private-key header shape'
    ELSE NULL
  END;
$$;

COMMENT ON FUNCTION brain.credential_shape(text) IS
  'P0: names the credential shape p_text matches, or NULL when it matches none. Credentials are '
  'referenced by Connect alias and never stored here -- this is the check that refuses one that '
  'was pasted in by mistake. Checked at least: URL user:password@, password=, sslpassword, '
  'api_key=, an sk- style key, a JWT (three base64url segments starting eyJ), a PEM private-key '
  'header. Deliberately over-inclusive the same way migration 72''s IMMUTABLE helpers are: a '
  'refused location that was never actually a secret costs a re-registration with the secret '
  'named by Connect alias instead; a stored secret costs a great deal more.';

REVOKE EXECUTE ON FUNCTION brain.credential_shape(text) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION brain.credential_shape(text) TO brain_runtime, brain_owner;

-- ---------------------------------------------------------------- brain.data_source

CREATE TABLE IF NOT EXISTS brain.data_source (
  id             serial PRIMARY KEY,
  name           text        NOT NULL UNIQUE,
  kind           text        NOT NULL,
  description    text,
  location       text        NOT NULL,
  stale_after    interval    NOT NULL DEFAULT interval '2 days',
  last_seen_at   timestamptz,
  last_row_count bigint,
  registered_by  text,
  registered_at  timestamptz NOT NULL DEFAULT now(),
  amended_by     text,
  amended_at     timestamptz,
  retired_at     timestamptz,
  CONSTRAINT data_source_kind_ck CHECK (kind IN ('live', 'database', 'sheet')),
  CONSTRAINT data_source_name_not_blank_ck CHECK (btrim(name) <> ''),
  CONSTRAINT data_source_location_not_blank_ck CHECK (btrim(location) <> ''),
  CONSTRAINT data_source_location_no_credential_ck
    CHECK (brain.credential_shape(location) IS NULL),
  CONSTRAINT data_source_description_no_credential_ck
    CHECK (brain.credential_shape(description) IS NULL)
);

COMMENT ON TABLE brain.data_source IS
  'The registry the data page reads (D-9: Postgres on the VPS is the source of truth, not Rowy). '
  'One row per source a human or a connector has told this database about. Never written '
  'directly: see brain.data_source_register() and brain.data_source_touch(), the two doors.';
COMMENT ON COLUMN brain.data_source.kind IS
  'One of the three tabs on the approved Data page mockup (Main.dc.html): live (a warehouse view '
  'or API read fresh each time), database (a brain/Postgres table read directly), sheet (a file '
  'kept outside Postgres -- Excel, CSV, Google Sheets). Not extensible without a migration on '
  'purpose: a fourth kind is a decision, not a string a form typed.';
COMMENT ON COLUMN brain.data_source.location IS
  'Where it lives, in the vocabulary the mockup already uses: "schema.table" for a database '
  'source, a warehouse view name for a live source ("warehouse.shop_orders"), a file name '
  'and tab for a sheet. Free text on purpose -- the three kinds do not share one addressing '
  'scheme -- but never blank (see the CHECK): a source with nowhere named is not registered.';
COMMENT ON COLUMN brain.data_source.stale_after IS
  'How long since last_seen_at before brain.data_source_health calls this row stale. Per-row '
  'because a live feed read every few minutes and a finance sheet updated weekly do not share '
  'one clock. Defaults to 2 days; a source with a slower natural cadence should register with a '
  'wider one rather than read as perpetually stale.';
COMMENT ON COLUMN brain.data_source.last_seen_at IS
  'When brain.data_source_touch() was last called for this source -- i.e. when something last '
  'actually read it, not when it was registered. NULL means registered but never read: that is '
  'the "never" state in brain.data_source_health, distinct from stale.';
COMMENT ON COLUMN brain.data_source.registered_by IS
  'brain.current_human() at registration time, or NULL when an agent/connector registered it. '
  'Attribution only, per this repo''s IDENTITY-POLICY tier 1 -- not a permission and not a lock '
  'on who may later retire or amend the row.';
COMMENT ON COLUMN brain.data_source.amended_by IS
  'P2: brain.current_human() at the time of the LAST amendment (a second or later call to '
  'brain.data_source_register() naming this source), stamped by the function and never supplied '
  'by a caller -- same non-forgeable shape as registered_by. NULL until the first amendment: a '
  'source registered once and never touched again has no amender to name.';
COMMENT ON COLUMN brain.data_source.amended_at IS
  'P2: when amended_by was last set. NULL exactly when amended_by is NULL. Distinct from '
  'last_seen_at (set by data_source_touch, meaning someone READ the source) -- this is when '
  'someone last changed its declared metadata.';

GRANT SELECT ON brain.data_source TO brain_runtime;

-- ---------------------------------------------------------------- brain.goal

CREATE TABLE IF NOT EXISTS brain.goal (
  id           serial PRIMARY KEY,
  name         text        NOT NULL,
  sub          text,
  target       text        NOT NULL,
  now_value    text        NOT NULL,
  unit         text,
  why          text,
  done_when    text,
  owner_human  text,
  period_label text,
  written_at   timestamptz NOT NULL DEFAULT now(),
  archived_at  timestamptz,
  CONSTRAINT goal_name_not_blank_ck CHECK (btrim(name) <> '')
);

COMMENT ON TABLE brain.goal IS
  'D-10: goals are children of the data section, not a top-level one. One row per goal on the '
  'approved Goals.dc.html mockup: target, current reading, why it was set, and when it counts as '
  'done. Written only through brain.goal_write().';
COMMENT ON COLUMN brain.goal.target IS
  'Text, not numeric, on purpose: the mockup''s targets are "82.0%", "1,400" and "260,000" -- '
  'three different shapes. A page that renders text does not need to know a goal''s unit to '
  'display it; a report that needs to COMPUTE against a goal reads unit separately and parses '
  'target itself. Revisit if a computed progress bar is ever built against this column (see the '
  'MUST-NOT-BUILD note below -- that decision has not been made and this file does not make it).';
COMMENT ON COLUMN brain.goal.owner_human IS
  'A human named the way brain.human_roster() names one, or NULL. Not a foreign key to '
  'brain.human_role by column type (that table is owner-only per migration 41), but should be '
  'checked against brain.human_roster() by brain.goal_write() before it is trusted anywhere else.';

GRANT SELECT ON brain.goal TO brain_runtime;

-- brain.goal <-> brain.data_source: "Measured from", the chips on the goal detail panel.

CREATE TABLE IF NOT EXISTS brain.goal_source (
  goal_id        integer NOT NULL REFERENCES brain.goal(id) ON DELETE CASCADE,
  data_source_id integer NOT NULL REFERENCES brain.data_source(id) ON DELETE RESTRICT,
  PRIMARY KEY (goal_id, data_source_id)
);

COMMENT ON TABLE brain.goal_source IS
  '"Measured from" on the Goals mockup detail panel. ON DELETE RESTRICT on the source side: a '
  'source that a goal is measured from cannot be silently dropped out from under it -- retire '
  'the source (data_source.retired_at) instead, which brain.data_source_health surfaces.';

GRANT SELECT ON brain.goal_source TO brain_runtime;

-- "Reported in" -- a label only, until Commander C's report entity exists. See header note.

CREATE TABLE IF NOT EXISTS brain.goal_reported_in (
  goal_id integer NOT NULL REFERENCES brain.goal(id) ON DELETE CASCADE,
  label   text    NOT NULL,
  PRIMARY KEY (goal_id, label)
);

COMMENT ON TABLE brain.goal_reported_in IS
  'PLACEHOLDER, named so it can be replaced by a real foreign key to a report entity without a '
  'rename, once Commander C''s reporting section exists (it does not, at fece8c3 -- '
  'W4-V002-DATA-CENSUS.md control: zero hits for starmynd-reporting in this repository). Until '
  'then this is exactly what the mockup shows: a chip with a name on it, nothing more.';

GRANT SELECT ON brain.goal_reported_in TO brain_runtime;

-- ---------------------------------------------------------------- brain.kpi

CREATE TABLE IF NOT EXISTS brain.kpi (
  id             serial PRIMARY KEY,
  name           text    NOT NULL,
  sub            text,
  target         text    NOT NULL,
  now_value      text    NOT NULL,
  unit           text,
  data_source_id integer REFERENCES brain.data_source(id) ON DELETE SET NULL,
  updated_at     timestamptz NOT NULL DEFAULT now(),
  archived_at    timestamptz,
  CONSTRAINT kpi_name_not_blank_ck CHECK (btrim(name) <> '')
);

COMMENT ON TABLE brain.kpi IS
  'D-10: a KPI is a child of the data section, same as a goal, but reads exactly ONE source (the '
  'mockup''s KPI rows each carry one "src" chip, never several) -- the difference from '
  'brain.goal_source''s many-to-many is deliberate, not an oversight to reconcile later. '
  'ON DELETE SET NULL on the source: a KPI whose source is retired keeps existing (its last '
  'reading stays legible) but stops being able to claim it is current.';

GRANT SELECT ON brain.kpi TO brain_runtime;

-- ---------------------------------------------------------------- the write doors (D00/V00: one
-- transition function per state change, exposed as a verb, called by every surface)

CREATE OR REPLACE FUNCTION brain.data_source_register(
  p_name        text,
  p_kind        text,
  p_location    text,
  p_description text    DEFAULT NULL,
  p_stale_after interval DEFAULT interval '2 days'
) RETURNS integer
  LANGUAGE plpgsql AS $$
DECLARE v_id integer; v_who text; v_shape text;
BEGIN
  -- P0: refuse a credential-shaped location or description, named, before anything is written.
  -- The CHECK constraints on the table catch the same thing for any OTHER writer (there should be
  -- none -- this is the one door -- but a constraint is cheap insurance against a future one), and
  -- are what actually fires when this function's own check is the thing removed, which is exactly
  -- the mutation the self-check below plants and proves against.
  v_shape := brain.credential_shape(p_location);
  IF v_shape IS NOT NULL THEN
    RAISE EXCEPTION 'brain.data_source_register: refusing location for %: it looks like %',
                    p_name, v_shape
      USING ERRCODE = 'check_violation',
            HINT = 'Credentials are referenced by a Connect alias, never stored in the data '
                   'source registry. Name the alias instead of the value.';
  END IF;
  v_shape := brain.credential_shape(p_description);
  IF v_shape IS NOT NULL THEN
    RAISE EXCEPTION 'brain.data_source_register: refusing description for %: it looks like %',
                    p_name, v_shape
      USING ERRCODE = 'check_violation',
            HINT = 'Credentials are referenced by a Connect alias, never stored in the data '
                   'source registry. Name the alias instead of the value.';
  END IF;

  v_who := brain.current_human();

  INSERT INTO brain.data_source (name, kind, location, description, stale_after, registered_by)
    VALUES (p_name, p_kind, p_location, p_description, p_stale_after, v_who)
  ON CONFLICT (name) DO UPDATE
    -- P2: a second (or later) registration is an AMENDMENT. amended_by/amended_at are stamped
    -- from the database's own answer, never from a caller, same shape as migration 72's
    -- last_human. registered_by/registered_at are the FIRST registration and are left alone here.
    SET kind = EXCLUDED.kind,
        location = EXCLUDED.location,
        description = EXCLUDED.description,
        stale_after = EXCLUDED.stale_after,
        retired_at = NULL,
        amended_by = v_who,
        amended_at = now()
    RETURNING id INTO v_id;
  RETURN v_id;
END $$;

COMMENT ON FUNCTION brain.data_source_register(text, text, text, text, interval) IS
  'The ONLY door that creates or amends a brain.data_source row''s declared metadata (what it is '
  'and where it lives). Idempotent on name: registering the same name again amends the row and '
  'un-retires it rather than erroring or duplicating; the amendment stamps amended_by/amended_at '
  'and leaves registered_by/registered_at as the first registration recorded them. Does NOT '
  'touch last_seen_at or last_row_count -- that is brain.data_source_touch()''s job, kept '
  'separate because "I am declaring this source exists" and "I just read it" are different facts '
  'with different callers. P0: refuses a location or description matching brain.credential_shape, '
  'named in the error, before either INSERT or UPDATE runs.';

REVOKE EXECUTE ON FUNCTION brain.data_source_register(text, text, text, text, interval) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION brain.data_source_register(text, text, text, text, interval)
  TO brain_runtime, brain_owner;

CREATE OR REPLACE FUNCTION brain.data_source_touch(
  p_name      text,
  p_row_count bigint DEFAULT NULL
) RETURNS void
  LANGUAGE sql AS $$
  UPDATE brain.data_source
     SET last_seen_at = now(),
         last_row_count = COALESCE(p_row_count, last_row_count)
   WHERE name = p_name;
$$;

COMMENT ON FUNCTION brain.data_source_touch(text, bigint) IS
  'The freshness stamp. Called by whatever actually reads a source -- a report render, a '
  'connector''s scheduled pull -- never by the registration step. This is what makes a source '
  'that stops being read fall behind stale_after and turn visibly stale/dead in '
  'brain.data_source_health, instead of looking exactly as current as one still being read.';

REVOKE EXECUTE ON FUNCTION brain.data_source_touch(text, bigint) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION brain.data_source_touch(text, bigint) TO brain_runtime, brain_owner;

CREATE OR REPLACE FUNCTION brain.goal_write(
  p_name        text,
  p_target      text,
  p_now_value   text,
  p_sub         text DEFAULT NULL,
  p_unit        text DEFAULT NULL,
  p_why         text DEFAULT NULL,
  p_done_when   text DEFAULT NULL,
  p_owner_human text DEFAULT NULL,
  p_period      text DEFAULT NULL,
  p_id          integer DEFAULT NULL
) RETURNS integer
  LANGUAGE plpgsql AS $$
DECLARE v_id integer;
BEGIN
  IF p_id IS NULL THEN
    INSERT INTO brain.goal (name, target, now_value, sub, unit, why, done_when, owner_human, period_label)
      VALUES (p_name, p_target, p_now_value, p_sub, p_unit, p_why, p_done_when, p_owner_human, p_period)
      RETURNING id INTO v_id;
  ELSE
    UPDATE brain.goal
       SET name = p_name, target = p_target, now_value = p_now_value, sub = p_sub, unit = p_unit,
           why = p_why, done_when = p_done_when, owner_human = p_owner_human, period_label = p_period
     WHERE id = p_id
     RETURNING id INTO v_id;
    IF v_id IS NULL THEN
      RAISE EXCEPTION 'brain.goal_write: no goal % to amend', p_id;
    END IF;
  END IF;
  RETURN v_id;
END $$;

COMMENT ON FUNCTION brain.goal_write(text, text, text, text, text, text, text, text, text, integer) IS
  'The one door for creating or amending a goal. p_id NULL creates; p_id set amends that row and '
  'raises if it does not exist, rather than silently creating a second one under a passed id.';

REVOKE EXECUTE ON FUNCTION brain.goal_write(text, text, text, text, text, text, text, text, text, integer) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION brain.goal_write(text, text, text, text, text, text, text, text, text, integer)
  TO brain_runtime, brain_owner;

CREATE OR REPLACE FUNCTION brain.kpi_upsert(
  p_name           text,
  p_target         text,
  p_now_value      text,
  p_sub            text    DEFAULT NULL,
  p_unit           text    DEFAULT NULL,
  p_source_name    text    DEFAULT NULL,
  p_id             integer DEFAULT NULL
) RETURNS integer
  LANGUAGE plpgsql AS $$
DECLARE v_id integer; v_source_id integer;
BEGIN
  IF p_source_name IS NOT NULL THEN
    SELECT id INTO v_source_id FROM brain.data_source WHERE name = p_source_name;
    IF v_source_id IS NULL THEN
      RAISE EXCEPTION 'brain.kpi_upsert: no data source named %; register it first', p_source_name
        USING HINT = 'call brain.data_source_register() before naming a source on a KPI';
    END IF;
  END IF;
  IF p_id IS NULL THEN
    INSERT INTO brain.kpi (name, target, now_value, sub, unit, data_source_id)
      VALUES (p_name, p_target, p_now_value, p_sub, p_unit, v_source_id)
      RETURNING id INTO v_id;
  ELSE
    UPDATE brain.kpi
       SET name = p_name, target = p_target, now_value = p_now_value, sub = p_sub, unit = p_unit,
           data_source_id = v_source_id, updated_at = now()
     WHERE id = p_id
     RETURNING id INTO v_id;
    IF v_id IS NULL THEN
      RAISE EXCEPTION 'brain.kpi_upsert: no kpi % to amend', p_id;
    END IF;
  END IF;
  RETURN v_id;
END $$;

COMMENT ON FUNCTION brain.kpi_upsert(text, text, text, text, text, text, integer) IS
  'The one door for creating or amending a KPI. Refuses a source name brain.data_source does not '
  'recognise (the same shape as migration 37''s roster check on assigned_human) rather than '
  'letting a KPI point at a source that was never registered.';

REVOKE EXECUTE ON FUNCTION brain.kpi_upsert(text, text, text, text, text, text, integer) FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION brain.kpi_upsert(text, text, text, text, text, text, integer)
  TO brain_runtime, brain_owner;

-- ---------------------------------------------------------------- brain.data_source_health

CREATE OR REPLACE VIEW brain.data_source_health AS
  SELECT
    d.*,
    CASE
      WHEN d.retired_at IS NOT NULL THEN 'retired'
      WHEN d.last_seen_at IS NULL THEN 'never'
      WHEN now() - d.last_seen_at > d.stale_after THEN 'stale'
      ELSE 'fresh'
    END AS status
  FROM brain.data_source d;

COMMENT ON VIEW brain.data_source_health IS
  'What makes the data page not age quietly (D2 item (e)). "never": registered, nothing has '
  'called brain.data_source_touch() for it yet. "stale": touched once, not within its own '
  'stale_after. "retired": retired_at is set. A page that reads this view rather than '
  'brain.data_source directly cannot render a dead source the same way it renders a live one.';

GRANT SELECT ON brain.data_source_health TO brain_runtime;

-- ---------------------------------------------------------------- self-check, imitating
-- migration 0072's refusal-and-control shape. Named refusals that must fire, named controls that
-- must pass, a reconciled count, fixture rows removed at the end. Runs as the applying user
-- (bootstrap superuser), which is why it calls the functions directly rather than switching role;
-- brain.current_human() reads session_user regardless of privilege, so v_who below is whatever
-- this migration is applied as (typically NULL, since the superuser is not in brain.human_role) --
-- that is the correct behaviour to prove, not a gap: an agent/superuser applying a migration is
-- not a human, and registered_by/amended_by should read NULL for it, same as migration 72's
-- pattern for a non-human connection.

DO $mv85$
DECLARE
  n int := 0; refusals int := 0; controls int := 0;
  fx text := '_mv85_fixture_source';
  v_id int; v_msg text; v_who text; v_amended_by text; v_amended_at timestamptz;
  v_last_seen timestamptz; v_row_count bigint; v_status text; v_refused boolean;
BEGIN
  -- Each refusal check below runs the attempt in its own BEGIN/EXCEPTION (setting v_refused/v_msg
  -- only) and THEN, OUTSIDE that block, checks v_refused and the message. This two-scope shape
  -- fixes this self-check's own first-draft bug: a RAISE EXCEPTION describing the failure, placed
  -- INSIDE the same BEGIN block whose EXCEPTION WHEN OTHERS was meant to catch only the function's
  -- raise, was caught by that same handler, and its own text happened to contain the expected
  -- substring -- so a REMOVED check would have misreported as a PASS. Keeping "did it raise at
  -- all" outside the handler means a missing refusal aborts the whole migration by name instead of
  -- being swallowed and misread as its own success.
  v_who := brain.current_human();

  -- ---- P0 refusals: seven credential shapes, each must fail BY NAME (not merely "an error").
  -- Shape: attempt in its own BEGIN/EXCEPTION (sets v_refused/v_msg only); THEN, OUTSIDE that
  -- block, check v_refused and the message. A mutation that removes the check makes v_refused
  -- stay false, and the "NOT refused" RAISE below is not caught by anything in this DO block, so
  -- it aborts the transaction with a name naming exactly which shape stopped being refused.

  v_refused := false; v_msg := NULL;
  BEGIN
    PERFORM brain.data_source_register(fx, 'database', 'postgres://user:hunter2@host/db', NULL);
  EXCEPTION WHEN OTHERS THEN
    v_refused := true; GET STACKED DIAGNOSTICS v_msg = MESSAGE_TEXT;
  END;
  IF NOT v_refused THEN
    RAISE EXCEPTION 'migration 85 self-check RED: a URL user:password@ location was NOT refused (credential check missing or defeated)';
  END IF;
  IF v_msg NOT LIKE '%URL user:password@%' THEN
    RAISE EXCEPTION 'migration 85 self-check RED: wrong refusal name for URL shape: %', v_msg;
  END IF;
  n := n + 1; refusals := refusals + 1;

  v_refused := false; v_msg := NULL;
  BEGIN
    PERFORM brain.data_source_register(fx, 'database', 'schema.table', 'password=hunter2');
  EXCEPTION WHEN OTHERS THEN
    v_refused := true; GET STACKED DIAGNOSTICS v_msg = MESSAGE_TEXT;
  END;
  IF NOT v_refused THEN
    RAISE EXCEPTION 'migration 85 self-check RED: a password= description was NOT refused (credential check missing or defeated)';
  END IF;
  IF v_msg NOT LIKE '%password=%' THEN
    RAISE EXCEPTION 'migration 85 self-check RED: wrong refusal name for password= shape: %', v_msg;
  END IF;
  n := n + 1; refusals := refusals + 1;

  v_refused := false; v_msg := NULL;
  BEGIN
    PERFORM brain.data_source_register(fx, 'database', 'schema.table', 'sslpassword=hunter2');
  EXCEPTION WHEN OTHERS THEN
    v_refused := true; GET STACKED DIAGNOSTICS v_msg = MESSAGE_TEXT;
  END;
  IF NOT v_refused THEN
    RAISE EXCEPTION 'migration 85 self-check RED: an sslpassword description was NOT refused (credential check missing or defeated)';
  END IF;
  IF v_msg NOT LIKE '%sslpassword%' THEN
    RAISE EXCEPTION 'migration 85 self-check RED: wrong refusal name for sslpassword shape: %', v_msg;
  END IF;
  n := n + 1; refusals := refusals + 1;

  v_refused := false; v_msg := NULL;
  BEGIN
    PERFORM brain.data_source_register(fx, 'database', 'schema.table', 'api_key=abcd1234efgh5678');
  EXCEPTION WHEN OTHERS THEN
    v_refused := true; GET STACKED DIAGNOSTICS v_msg = MESSAGE_TEXT;
  END;
  IF NOT v_refused THEN
    RAISE EXCEPTION 'migration 85 self-check RED: an api_key= description was NOT refused (credential check missing or defeated)';
  END IF;
  IF v_msg NOT LIKE '%api_key=%' THEN
    RAISE EXCEPTION 'migration 85 self-check RED: wrong refusal name for api_key= shape: %', v_msg;
  END IF;
  n := n + 1; refusals := refusals + 1;

  v_refused := false; v_msg := NULL;
  BEGIN
    PERFORM brain.data_source_register(fx, 'database', 'schema.table',
      'key sk-abcdefghijklmnopqrstuvwx');
  EXCEPTION WHEN OTHERS THEN
    v_refused := true; GET STACKED DIAGNOSTICS v_msg = MESSAGE_TEXT;
  END;
  IF NOT v_refused THEN
    RAISE EXCEPTION 'migration 85 self-check RED: an sk- style key description was NOT refused (credential check missing or defeated)';
  END IF;
  IF v_msg NOT LIKE '%sk- style API key%' THEN
    RAISE EXCEPTION 'migration 85 self-check RED: wrong refusal name for sk- shape: %', v_msg;
  END IF;
  n := n + 1; refusals := refusals + 1;

  v_refused := false; v_msg := NULL;
  BEGIN
    PERFORM brain.data_source_register(fx, 'database', 'schema.table',
      'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123signature');
  EXCEPTION WHEN OTHERS THEN
    v_refused := true; GET STACKED DIAGNOSTICS v_msg = MESSAGE_TEXT;
  END;
  IF NOT v_refused THEN
    RAISE EXCEPTION 'migration 85 self-check RED: a JWT-shaped description was NOT refused (credential check missing or defeated)';
  END IF;
  IF v_msg NOT LIKE '%JWT shape%' THEN
    RAISE EXCEPTION 'migration 85 self-check RED: wrong refusal name for JWT shape: %', v_msg;
  END IF;
  n := n + 1; refusals := refusals + 1;

  v_refused := false; v_msg := NULL;
  BEGIN
    PERFORM brain.data_source_register(fx, 'database', 'schema.table',
      E'-----BEGIN RSA PRIVATE KEY-----\nMIIB...');
  EXCEPTION WHEN OTHERS THEN
    v_refused := true; GET STACKED DIAGNOSTICS v_msg = MESSAGE_TEXT;
  END;
  IF NOT v_refused THEN
    RAISE EXCEPTION 'migration 85 self-check RED: a private-key header description was NOT refused (credential check missing or defeated)';
  END IF;
  IF v_msg NOT LIKE '%private-key header%' THEN
    RAISE EXCEPTION 'migration 85 self-check RED: wrong refusal name for private-key shape: %', v_msg;
  END IF;
  n := n + 1; refusals := refusals + 1;

  -- Prove the table has no fixture row left over from any of the seven refused attempts: every
  -- refusal above raised inside its own subtransaction, which Postgres rolls back on exception,
  -- so none of them should have left a row even though the INSERT was attempted.
  PERFORM 1 FROM brain.data_source WHERE name = fx;
  IF FOUND THEN
    RAISE EXCEPTION 'migration 85 self-check RED: a refused registration left a row behind';
  END IF;
  n := n + 1; controls := controls + 1;

  -- ---- controls: ordinary registration, P2 amend stamping, touch, and freshness

  -- control: an ordinary (non-credential) registration succeeds and amended_* start NULL.
  v_id := brain.data_source_register(fx, 'database', 'brain_scratch.some_table',
                                      'fixture for migration 85 self-check');
  SELECT registered_by, amended_by, amended_at INTO v_who, v_amended_by, v_amended_at
    FROM brain.data_source WHERE id = v_id;
  IF v_amended_by IS NOT NULL OR v_amended_at IS NOT NULL THEN
    RAISE EXCEPTION 'migration 85 self-check RED: a first registration already carried an amender (%)',
      v_amended_by;
  END IF;
  n := n + 1; controls := controls + 1;

  -- control: a second registration of the SAME name amends and stamps amended_by/amended_at.
  PERFORM pg_sleep(0.01); -- so amended_at is measurably later than registered_at on a fast box
  PERFORM brain.data_source_register(fx, 'database', 'brain_scratch.some_table_v2',
                                      'fixture for migration 85 self-check, amended');
  SELECT amended_by, amended_at INTO v_amended_by, v_amended_at
    FROM brain.data_source WHERE id = v_id;
  IF v_amended_at IS NULL THEN
    RAISE EXCEPTION 'migration 85 self-check RED: a second registration did not stamp amended_at';
  END IF;
  -- amended_by matches whatever brain.current_human() answers for THIS connection (v_who from
  -- above, captured from the same function on the same session) -- not a caller-supplied name.
  IF v_amended_by IS DISTINCT FROM v_who THEN
    RAISE EXCEPTION 'migration 85 self-check RED: amended_by (%) does not match brain.current_human() (%)',
      v_amended_by, v_who;
  END IF;
  n := n + 1; controls := controls + 1;

  -- control: data_source_touch sets last_seen_at and last_row_count, and status reads 'fresh'.
  PERFORM brain.data_source_touch(fx, 42);
  SELECT last_seen_at, last_row_count INTO v_last_seen, v_row_count
    FROM brain.data_source WHERE id = v_id;
  IF v_last_seen IS NULL OR v_row_count IS DISTINCT FROM 42 THEN
    RAISE EXCEPTION 'migration 85 self-check RED: data_source_touch did not stamp last_seen_at/last_row_count';
  END IF;
  SELECT status INTO v_status FROM brain.data_source_health WHERE id = v_id;
  IF v_status IS DISTINCT FROM 'fresh' THEN
    RAISE EXCEPTION 'migration 85 self-check RED: a just-touched source read status % (expected fresh)',
      v_status;
  END IF;
  n := n + 1; controls := controls + 1;

  -- control: an UNTOUCHED source reads 'never', and a source touched long ago reads 'stale'.
  PERFORM brain.data_source_register(fx || '_never', 'sheet', 'nowhere.xlsx, tab 1', NULL);
  SELECT status INTO v_status FROM brain.data_source_health WHERE name = fx || '_never';
  IF v_status IS DISTINCT FROM 'never' THEN
    RAISE EXCEPTION 'migration 85 self-check RED: a never-touched source read status % (expected never)',
      v_status;
  END IF;
  UPDATE brain.data_source SET last_seen_at = now() - interval '3 days'
    WHERE id = v_id; -- default stale_after is 2 days
  SELECT status INTO v_status FROM brain.data_source_health WHERE id = v_id;
  IF v_status IS DISTINCT FROM 'stale' THEN
    RAISE EXCEPTION 'migration 85 self-check RED: a source last seen 3 days ago read status % (expected stale)',
      v_status;
  END IF;
  n := n + 1; controls := controls + 1;

  -- control: brain.kpi_upsert refuses a source name the registry does not recognise, named.
  v_refused := false; v_msg := NULL;
  BEGIN
    PERFORM brain.kpi_upsert('mv85 fixture kpi', '100', '90', NULL, NULL, fx || '_does_not_exist');
  EXCEPTION WHEN OTHERS THEN
    v_refused := true; GET STACKED DIAGNOSTICS v_msg = MESSAGE_TEXT;
  END;
  IF NOT v_refused THEN
    RAISE EXCEPTION 'migration 85 self-check RED: kpi_upsert accepted an unregistered source name';
  END IF;
  IF v_msg NOT LIKE '%no data source named%' THEN
    RAISE EXCEPTION 'migration 85 self-check RED: wrong refusal name for kpi_upsert: %', v_msg;
  END IF;
  n := n + 1; controls := controls + 1;

  -- ---- fixture cleanup, so scratch stays clean for whatever runs next

  DELETE FROM brain.kpi WHERE name = 'mv85 fixture kpi';
  DELETE FROM brain.data_source WHERE name IN (fx, fx || '_never');

  IF n <> 13 OR refusals <> 7 OR controls <> 6 THEN
    RAISE EXCEPTION 'migration 85 self-check RED: expected 13 checks as 7 refusals and 6 controls, ran % as % and %',
      n, refusals, controls;
  END IF;
  RAISE NOTICE 'migration 85 self-check: % of 13 checks passed (% refusals fired by name, % '
               'controls passed): URL user:password@, password=, sslpassword, api_key=, sk- key, '
               'JWT and private-key-header locations/descriptions were all refused by name; no '
               'row was left by a refused attempt; a first registration left amended_by/at NULL; '
               'a second registration stamped them from brain.current_human(); '
               'data_source_touch stamped last_seen_at/last_row_count and read fresh; an '
               'untouched source read never and an old one read stale; kpi_upsert refused an '
               'unregistered source name. Fixture rows removed.', n, refusals, controls;
END $mv85$;

INSERT INTO brain.schema_migration (version, name)
  VALUES (85, '0085_a_data_source_is_registered_and_read')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
