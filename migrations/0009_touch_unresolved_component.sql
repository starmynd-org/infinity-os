-- migration 9: `brain.touch.entity_id` becomes nullable, and the raw component reference
--              lands beside it. The projection becomes TOTAL over what git can hold.
--
-- Task 0114, filed by the adapter lane out of 0103 and dispatched to the store lane because the
-- store lane owns the column list. It is a schema DECISION, and the decision is recorded here
-- rather than in a report, because a later lane meeting a null `entity_id` will open this file
-- and must not have to guess.
--
--
-- WHAT WAS TRUE BEFORE THIS FILE
-- ------------------------------
-- `touch add --allow-unresolved` can commit a component-touch edge whose `entity_id` is null.
-- That is not hypothetical and it is not a bug in the adapter -- it is the path D2 built on
-- purpose, so that a reference the brain index cannot name is preserved raw instead of being
-- given a FABRICATED id. Committed evidence, re-read from the commit rather than quoted from a
-- handoff:
--
--     git -C /home/you/.cache/d2-scratch-wt show \
--       4ba53bf8:departments/warner-sandbox/receipts/2026-08-16-d2-touch-edges-followon.md
--
--       - subject_type: "receipt"
--         entity_id: null
--         component_type: "rule"
--         entity_ref: "retrieval-load-order-policy"
--         resolution_status: "unresolved"
--
-- and a SECOND edge in the same receipt that resolved cleanly. So the real case is the mixed
-- one, and it is the mixed one that made the old behaviour expensive: migration 1 made
-- `entity_id` NOT NULL, so `store_projection.py` refused the WHOLE receipt on meeting the null
-- edge, and the resolved edge beside it never landed either. A receipt booked with
-- `--allow-unresolved` could never be projected at all.
--
-- That refusal was the SAFE behaviour and it was not the RIGHT one. It was safe because a
-- partially projected receipt is indistinguishable from a complete one on read, so landing four
-- edges of five and saying nothing would be worse than landing none. It was not right because
-- it left `brain.touch` unable to hold something git holds, and made this sentence -- the one
-- `store_projection.py` uses to define what a projection IS -- false:
--
--     "Drop brain.touch and brain.receipt and re-run this loader over the same commits and you
--      get the same rows back. That is what makes it a projection."
--
--
-- THE THREE OPTIONS, AND WHY THIS ONE
-- -----------------------------------
-- 0114 named three and asked for one. Written down because the two that were not taken are the
-- ones a later reader will re-propose.
--
--   1. NULLABLE `entity_id`, plus `entity_ref` and `entity_resolution_status`.   <-- TAKEN
--   2. Leave it NOT NULL and treat an unresolved edge as un-projectable by design, with a
--      reconciliation report naming the gap.
--   3. Refuse `--allow-unresolved` on `touch add`, so git never holds one.
--
-- Option 1 was taken for three reasons, in order of weight.
--
-- (a) THE STORE ALREADY CARRIES AN UNRESOLVED PRODUCER. Migration 5 answered this exact
--     question for `produced_by` on all 22 tables that have it: SQL NULL for the id, the raw
--     string in `produced_by_ref`, the reason in `resolution_status`. `brain.receipt` holds a
--     live row shaped that way (`produced_by_ref = '[[no-such-node-t5-lineage-probe]]'`,
--     `resolution_status = 'unresolved'`). Carrying an unresolved PRODUCER while refusing an
--     unresolved COMPONENT is an inconsistency, not a principle. This migration is migration 5
--     applied to the one column migration 5 did not reach, and it uses the same three-part
--     shape on purpose so there is one pattern in this schema rather than two.
--
-- (b) OPTION 3 CANNOT HOLD THE INVARIANT IT PROMISES. It is the cleanest-sounding of the three
--     -- if git never holds a null, the store never meets one -- but git is the source and a
--     receipt is a markdown file. Refusing the flag stops ONE WRITER, not the source. A
--     projection that is total only because a single tool is well-behaved is the failure mode
--     wearing the clothes of the fix, and the first hand-written receipt breaks it. It would
--     also discard the case D2 built the flag for: an index is a snapshot and can be stale,
--     while the component that shaped the work was real either way.
--
-- (c) OPTION 2 IS THE CHEAPEST AND IT PRICES THE WRONG THING. It costs nothing today and makes
--     the store a lossy view of git in one specific way, which then has to be documented,
--     remembered, and honoured by every later reader. WAGER-2c scores the SET of components
--     that shaped an action. An edge dropped for being unresolved does not make the set
--     smaller, it makes it WRONG, and it is wrong silently and in the direction of
--     under-counting. The good half of option 2 -- name the gap -- is kept here as
--     `brain.touch_unresolved` rather than discarded with the rest of it.
--
-- WHAT THIS DOES NOT CHANGE, AND MUST NOT BE READ AS CHANGING. Nullable `entity_id` is NOT
-- permission to write a touch row with no far end, and it is NOT a softening of the rule
-- against invented ids. It is the opposite: the null is what makes the invented id
-- unnecessary. `touch_names_a_far_end` and `touch_entity_resolution_coherent` below make both
-- of those structural rather than hoped for.
--
--
-- THE COST 0114 UNDER-PRICED, WHICH IS THE REASON THIS FILE IS LONGER THAN ITS DDL
-- -------------------------------------------------------------------------------
-- 0114 says the widened key "is already NULLS NOT DISTINCT, so this works". It is the reverse:
-- NULLS NOT DISTINCT is exactly what BREAKS it. Migration 4's key is
--
--     UNIQUE NULLS NOT DISTINCT (subject_type, subject_id, entity_id, component_type,
--                                orient_role, role)
--
-- Make `entity_id` nullable and leave that key alone, and two edges naming two DIFFERENT
-- unresolved refs -- say `retrieval-load-order-policy` and `some-other-rule`, both
-- `rule`/`substrate`/`load-bearing` on the same subject -- both carry `entity_id = NULL`.
-- NULLS NOT DISTINCT treats those NULLs as equal, the rows collide, and the loader's
-- `ON CONFLICT ... DO NOTHING` drops the second one WITHOUT ERROR. That is the same silent
-- dedup loss migration 4 added NULLS NOT DISTINCT to PREVENT, arriving from the other side:
-- there it protected D00-shaped rows from losing their dedup, here it would destroy two
-- distinct edges by over-deduping them.
--
-- So the edge's identity has to say what the edge actually points at. When `entity_id` is
-- present that is the id; when it is absent it is the raw ref, because the raw ref is all the
-- far end there is. `entity_key` below is that, as a stored generated column.
--
-- WHY A GENERATED COLUMN AND NOT JUST `entity_ref` IN THE KEY. Adding `entity_ref` to the key
-- directly would fix the null case and break the resolved one: the same component named two
-- ways (its id in one receipt, an alias in another) resolves to ONE `entity_id` and would then
-- land as TWO rows, double-counting that component's influence in anything scoring off these
-- edges. `COALESCE(entity_id, ...)` keeps resolved edges deduping on the id exactly as they do
-- today -- whichever ref named them -- and gives unresolved edges an identity of their own.
-- The `unresolved-ref:` prefix keeps a raw ref from ever colliding with a real id; brain ids
-- are kebab-case or `BOYD-*` and carry no colon.
--
-- The constraint keeps the NAME `touch_edge_key`, so `store_projection.py`'s
-- `ON CONFLICT ON CONSTRAINT touch_edge_key` keeps working unedited.
--
--
-- SAFETY, MEASURED BEFORE WRITING
-- -------------------------------
-- Every change here is widening. Relaxing NOT NULL, adding nullable columns, and replacing a
-- unique key with one that separates rows the old one merged can only ever ACCEPT MORE rows
-- than before, never fewer, so no row that exists today can be invalidated by applying this.
--
-- `brain.touch` HOLDS 5 ROWS AND `brain.receipt` HOLDS 4. Migrations 4 and 5 could each open
-- with "brain.touch was verified EMPTY (0 rows)" and that sentence is NO LONGER TRUE. It is
-- recorded here because the next migration that wants to add a NOT NULL column to this table
-- will inherit the opposite situation from the one those two files describe, and reading them
-- for precedent without reading this line is how a migration gets written that cannot apply.
-- All 5 existing rows carry a non-null `entity_id` and a null `entity_ref`, so `entity_key`
-- backfills to `entity_id` for every one of them and the re-added key is byte-identical in
-- effect to the old one over the existing data.
--
-- Re-runnable: every ADD COLUMN is IF NOT EXISTS and every constraint is dropped by name first.

\set ON_ERROR_STOP on

BEGIN;

SET search_path TO brain, public;

-- ---------------------------------------------------------------- version guard
--
-- Migration 5's raising guard, not 0101's silent `ON CONFLICT DO NOTHING` skip.
--
-- THIS GUARD FIRED FOR REAL WHILE THIS FILE WAS BEING WRITTEN, and the event is worth more than
-- the argument for it. This file was numbered 0008, and the number was claimed on the swarm bus
-- first with a message to both other live store-lane terminals. It was not enough: T3 applied
-- `0008_queue_defer_lineage` at 13:39:33Z, between the claim and the first dry run. The dry run
-- raised
--
--     ERROR: 0008_touch_unresolved_component: version 8 is already held by
--            0008_queue_defer_lineage
--
-- and this file was renumbered to 9. Under 0101's `ON CONFLICT (version) DO NOTHING` foot the
-- same run would have applied every ALTER above, silently skipped its ledger row, and exited 0
-- -- leaving a store carrying migration 9's schema while reporting migration 8, with no record
-- anywhere that the two had ever diverged. Announcing a version on a bus is a courtesy; the
-- guard is the control.

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 9;
  IF taken IS NOT NULL AND taken <> '0009_touch_unresolved_component' THEN
    RAISE EXCEPTION
      '0009_touch_unresolved_component: version 9 is already held by %. Another lane took this '
      'number while this file was being written. Renumber from brain.schema_migration (never '
      'from ls migrations/) and re-run.', taken;
  END IF;
END $$;

-- ---------------------------------------------------------------- the two columns

ALTER TABLE brain.touch ADD COLUMN IF NOT EXISTS entity_ref text;
ALTER TABLE brain.touch ADD COLUMN IF NOT EXISTS entity_resolution_status text;

-- ---------------------------------------------------------------- the null itself
--
-- The whole point of the file, and one line. Everything above and below exists to make sure
-- this line cannot be read as permission to write an edge that points at nothing.

ALTER TABLE brain.touch ALTER COLUMN entity_id DROP NOT NULL;

-- ---------------------------------------------------------------- an edge has a far end
--
-- An edge that names neither an id nor a ref is not an edge, it is a row. This is the one thing
-- the old NOT NULL was really buying and it is kept, at the correct width: the far end must be
-- NAMED, it need not be RESOLVED.

ALTER TABLE brain.touch DROP CONSTRAINT IF EXISTS touch_names_a_far_end;
ALTER TABLE brain.touch ADD CONSTRAINT touch_names_a_far_end CHECK (
  entity_id IS NOT NULL OR entity_ref IS NOT NULL);

-- ---------------------------------------------------------------- the closed vocabulary
--
-- The same three values as `<table>_resolution_status_enum` in migration 5, deliberately not a
-- shared domain: these two statuses answer different questions on the same row and a domain
-- would invite reading one for the other.
--
-- Honest about its own weight: the coherence check below is STRICTER than this one on every
-- row, so nothing reaches this constraint that coherence would have let through, and no
-- negative test can make this one fire alone. It is kept anyway for two reasons and neither is
-- belt-and-braces -- it puts the vocabulary in `\d brain.touch` where a reader will actually
-- meet it, and it holds the floor if a later migration loosens the coherence rule.

ALTER TABLE brain.touch DROP CONSTRAINT IF EXISTS touch_entity_resolution_status_enum;
ALTER TABLE brain.touch ADD CONSTRAINT touch_entity_resolution_status_enum CHECK (
  entity_resolution_status IS NULL
  OR entity_resolution_status IN ('resolved', 'ambiguous', 'unresolved'));

-- ---------------------------------------------------------------- the lie-detector
--
-- Migration 5's coherence check, aimed at the component instead of the producer, and STRICTER
-- in one direction on purpose.
--
--   entity_id NULL      requires a stated reason ('unresolved' or 'ambiguous'). Migration 5
--                       tolerates a NULL status beside a NULL id because a producer NAME is a
--                       real fourth state there ('d3-ingest' never asked the brain). There is
--                       no such state for a component: `touch add` ALWAYS resolves, so an edge
--                       with no id and no reason is a bug, not a stamp, and is refused.
--   entity_id NOT NULL  may say 'resolved' or say nothing. Nothing is what the 5 rows already
--                       in this table say, and what every row written before this migration
--                       says, so tolerating it is what keeps this file additive.
--   entity_id NOT NULL  may NEVER say 'unresolved' or 'ambiguous'. That combination is a
--                       FABRICATED ID -- a lookup that found nothing or found several, with an
--                       id written down anyway -- and it is the exact failure D2's unresolvable
--                       path exists to prevent. A fabricated id is believed later, which is
--                       strictly worse than a null.

-- `IS NOT DISTINCT FROM`, and the `IS NOT NULL` in the first arm, are not decoration. THE FIRST
-- DRAFT OF THIS CONSTRAINT WAS WRITTEN AS A PLAIN `entity_resolution_status IN ('unresolved',
-- 'ambiguous')` AND A NEGATIVE TEST CAUGHT IT ACCEPTING A NULL ID WITH A NULL STATUS -- the row
-- this arm exists to refuse. `NULL IN (...)` evaluates to UNKNOWN, not false, and a CHECK
-- constraint is SATISFIED by UNKNOWN. So the strict-looking arm was silently permissive for
-- exactly the value it was aimed at. Recorded because the next person to edit a CHECK in this
-- schema is one keystroke from the same hole, and it does not show up in a positive test.

ALTER TABLE brain.touch DROP CONSTRAINT IF EXISTS touch_entity_resolution_coherent;
ALTER TABLE brain.touch ADD CONSTRAINT touch_entity_resolution_coherent CHECK (
  CASE
    WHEN entity_id IS NULL
      THEN entity_resolution_status IS NOT NULL
       AND entity_resolution_status IN ('unresolved', 'ambiguous')
    ELSE entity_resolution_status IS NULL
      OR entity_resolution_status = 'resolved'
  END);

-- ---------------------------------------------------------------- the edge's identity
--
-- See the header. `entity_key` is what the edge points at: the id when there is one, the raw
-- reference when there is not. Stored and generated, so no writer can set it, get it wrong, or
-- forget it, and so the loader needs no edit at all.

ALTER TABLE brain.touch ADD COLUMN IF NOT EXISTS entity_key text
  GENERATED ALWAYS AS (COALESCE(entity_id, 'unresolved-ref:' || entity_ref)) STORED;

ALTER TABLE brain.touch DROP CONSTRAINT IF EXISTS touch_edge_key;
ALTER TABLE brain.touch ADD CONSTRAINT touch_edge_key
  UNIQUE NULLS NOT DISTINCT (subject_type, subject_id, entity_key, component_type,
                             orient_role, role);

-- The reconciliation lookup: every edge still waiting on a name. Partial, because the rows it
-- serves are the exception and the full index would be paid for on every insert.
CREATE INDEX IF NOT EXISTS touch_unresolved_ref_idx ON brain.touch (entity_ref)
  WHERE entity_id IS NULL;

-- ---------------------------------------------------------------- naming the gap
--
-- The half of option 2 worth keeping. A null `entity_id` is a debt, not a resolution: the edge
-- is real and the component is not yet nameable. This view is where that debt is visible, so
-- "the store carries it" never quietly becomes "the store is fine with it".
--
-- HOW SUCH A ROW IS CLEARED, and this is the sentence 0114 asked for above all others: NEVER by
-- an UPDATE that fills in an id. git holds the edge (WAGER-7) and the store is a copy of git;
-- an id typed into this table exists nowhere in git, is reconcilable against nothing, and makes
-- this table authoritative for lineage, at which point "losing the store costs zero knowledge"
-- is false. The route is: name the component in the brain, book a FOLLOW-ON receipt whose edge
-- resolves (receipts are append-only, WAGER-13a), and project that commit. The unresolved row
-- stays, because it is a true record of what was known when the work happened.

CREATE OR REPLACE VIEW brain.touch_unresolved AS
  SELECT id, subject_type, subject_id, entity_ref, entity_resolution_status,
         component_type, orient_role, role, git_ref, touched_at
    FROM brain.touch
   WHERE entity_id IS NULL;

COMMENT ON VIEW brain.touch_unresolved IS
  'Component-touch edges git holds whose component the brain could not name. Not an error '
  'queue and not a to-do list: a true record of what was resolvable when the work happened. '
  'Clear one by naming the component and booking a FOLLOW-ON receipt that resolves, never by '
  'UPDATEing an id into brain.touch -- an id that exists only here is reconcilable against '
  'nothing and makes this table authoritative for lineage.';

GRANT SELECT ON brain.touch_unresolved TO brain_runtime;

-- ---------------------------------------------------------------- the same notes, in the store
--
-- Migration 4's convention: a reader who reaches this table through `\d+ brain.touch` has not
-- opened this file.

COMMENT ON COLUMN brain.touch.entity_id IS
  'WAGER-2c''s `component_id`. Text, not a foreign key, for the reason migration 1 records. '
  'NULLABLE since migration 9: git can hold a component that did not resolve and this table '
  'now holds it too, with the raw reference in `entity_ref`. NULL is never an invitation to '
  'fill in an id -- it is what makes an invented id unnecessary.';

COMMENT ON COLUMN brain.touch.entity_ref IS
  'The raw component reference as the receipt in git wrote it. On an unresolved or ambiguous '
  'edge this is the whole far end of the edge and the only thing left of the claim. Distinct '
  'from `produced_by_ref`, which is about what RECORDED the touching, not what was TOUCHED.';

COMMENT ON COLUMN brain.touch.entity_resolution_status IS
  'resolved | ambiguous | unresolved | NULL, about `entity_id`. NOT the same column as '
  '`resolution_status`, which is about `produced_by` -- one row carries both because it makes '
  'two separate claims. NULL means the edge predates migration 9 or was written resolved '
  'without saying so; it is NOT a synonym for unresolved, and an edge with a NULL id may not '
  'use it. ambiguous keeps its candidates in the git receipt, not here.';

COMMENT ON COLUMN brain.touch.entity_key IS
  'What the edge points at, generated: the id when resolved, ''unresolved-ref:''||ref when not. '
  'Exists so the unique key can tell two unresolved edges apart. Without it they both key on a '
  'NULL entity_id, NULLS NOT DISTINCT collides them, and the loader''s ON CONFLICT DO NOTHING '
  'drops one silently. Generated so no writer can set it or forget it.';

COMMENT ON CONSTRAINT touch_edge_key ON brain.touch IS
  'The edge''s identity, matched to git''s. Keyed on `entity_key` rather than `entity_id` since '
  'migration 9, so resolved edges still dedup on the id whichever reference named them, and '
  'unresolved edges dedup on their reference instead of on each other.';

COMMENT ON CONSTRAINT touch_names_a_far_end ON brain.touch IS
  'An edge that names neither an id nor a reference points at nothing. This is what migration '
  '1''s NOT NULL was really buying, kept at the correct width: the far end must be NAMED, it '
  'need not be RESOLVED.';

COMMENT ON CONSTRAINT touch_entity_resolution_coherent ON brain.touch IS
  'Refuses a fabricated component id (an id beside ''unresolved'' or ''ambiguous'') and refuses '
  'a null id with no stated reason. Stricter than migration 5''s producer check in the second '
  'direction, because `touch add` always resolves and so there is no producer-name state here.';

-- No new grants. 0002_roles grants are TABLE-level on brain.touch (SELECT, INSERT, UPDATE to
-- brain_runtime, DELETE to nobody), so the new columns inherit. Verified, not assumed:
-- `grep -n "brain.touch" migrations/0002_roles.sql` shows it inside a table list with no
-- column list.

INSERT INTO brain.schema_migration (version, name) VALUES (9, '0009_touch_unresolved_component')
  ON CONFLICT (version) DO NOTHING;

COMMIT;
