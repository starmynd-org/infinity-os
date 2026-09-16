-- 0011_null_branch_residue_closure.sql -- on a FLAGGED task, a default must be a null branch and
--                                         NOTHING ELSE. The gate stops asking which verbs are
--                                         dangerous and starts asking which words it recognises.
--
-- Task 0154 (child of 0145), decided by the operator on 2026-08-16 as option (C). Additive and
-- re-runnable: it adds five functions and one view, redefines one trigger function, drops nothing
-- and edits no applied file. `0009_null_branch_act_scan.sql` stays exactly as it is and
-- `brain.default_is_null_branch` is NOT touched -- an unflagged task's default may still say
-- anything, which is what "silence is a usable answer" means for reversible work.
--
-- ================================================================= what was still broken
--
-- 0009 inverted the classifier to an act scan over the whole string, which closed every bypass
-- the D9 pass found. What it could not close, measured on scratch `brain_fix0145` at ledger 11 by
-- task 0145 and RE-MEASURED at ledger 16 by task 0154 (unchanged by the four migrations that
-- landed in between), is the SYNONYM class: an act whose verb is simply not in the vocabulary.
--
--     t | {} | hold; get the cheque to Mick
--     t | {} | no action; the client hears from us Monday
--     t | {} | no action; delivery to Mick Monday          (nominalised, no verb at all)
--     t | {} | hold; envoyer la facture a Mick             (not English)
--     t | {} | no action; it goes to Mick at 9
--     t | {} | hold. rm -rf the staging bucket             (a shell command, no English verb)
--
-- The second column is `brain.act_verbs_in`: it finds NOTHING in any of them. Each would fire at
-- a checkpoint on an `external` task, and neither `queue_default_event.shipped_act_verbs` nor
-- `brain.queue_default_breach` could see it, because both are built on the same vocabulary that
-- has the gap. A denylist over an open vocabulary is unbounded by construction: adding `get`,
-- `hear` and `go` refuses honest null branches ("hold until I hear from you"), and the next
-- attacker picks the next synonym.
--
-- ================================================================= the new rule, in one line
--
--     ON A FLAGGED TASK, EVERY WORD OF THE DEFAULT MUST BE A WORD THIS SYSTEM RECOGNISES AS
--     BELONGING TO A NULL BRANCH. Anything left over is residue, and residue is refused.
--
-- Normalise (reusing 0009's de-obfuscation, so a homoglyph cannot re-enter as a familiar token),
-- strip the null-branch content words, strip a CLOSED class of function words, and refuse on any
-- remainder, NAMING the words it did not recognise.
--
-- WHY THIS CLOSES THE SYNONYM CLASS WITHOUT NAMING A SINGLE SYNONYM. What carries the act in
-- `hold; get the cheque to Mick` was never the verb. It is `cheque` and `Mick`: an OBJECT and a
-- RECIPIENT, and an object and a recipient are residue by construction. A denylist has to decide
-- about every verb an attacker might reach for. This shape never has to decide about any of them:
-- `envoyer`, `rm`, `delivery` and the next synonym are all residue WITHOUT ANYONE NAMING THEM.
--
-- ================================================================= WHAT IT IS NOT
--
-- IT IS NOT "TRUE BY CONSTRUCTION", and this file is where that lesson is written down rather
-- than in a report nobody opens. Task 0154's brief claimed this shape makes *silence can only
-- ever ship the reversible branch* true by construction. It does not. Attacked with twenty
-- strings built ONLY from words the closed lists allow, the residue check accepted SEVENTEEN,
-- every one of them a BARE IMPERATIVE WITH A PRONOUN OBJECT:
--
--     no action; do it        hold; make it so        no action; proceed
--     hold; you know what to do                       hold; you take it from here
--
-- Those name no object and no recipient, so a residue check is blind to them BY CONSTRUCTION.
-- On a task whose brief already says what to do, `do it` ships exactly the act the gate exists to
-- stop. That is why `brain.bare_imperative_in` below is part of the rule and not a decoration:
-- a clause may not OPEN with a verb that is not a null-branch verb. `do not send` is legal
-- because `do` there is the auxiliary of a negator; `do it` is not, because `do` there is the
-- main verb. That distinction is one line of principle rather than one more open word list.
--
-- THE RESIDUAL THAT SURVIVES, named here so it is a finding and not a surprise:
--
--     ACCEPTED, and it should not be:   `no action; all of it`
--
-- It is a bare imperative whose head is `no` (legal) and whose act is carried entirely by a
-- quantifier and a pronoun, so it names no object for the residue check to see and opens no
-- clause for the imperative check to see. It is the last measured member of the bare-imperative
-- class, which is tracked as its own open task (swarm 0204) rather than folded in here and
-- called closed. One unsafe accept that is written down is a finding; one that is described as
-- none is the thing this program exists to prevent.
--
-- Measured on a 64-string battery, as unsafe accepts / false refusals:
--
--     shipped (0009 alone)                    23 / 0
--     residue only, light verbs allowed       17 / 0
--     residue only, no light verbs             3 / 5
--     THIS FILE (residue + no bare imperative) 1 / 5     <- option (C)
--     residue with light verbs + imperative    4 / 0
--
-- ================================================================= WHAT IT COSTS, stated
--
-- Five honest phrasings are now refused on a flagged task, and one of them was this system's own
-- suggested text:
--
--     take no action                                 <- was in `defaults.SUGGESTIONS`
--     hold until I hear from you
--     hold, it goes back in the queue
--     no action; nothing happens until you answer
--     stage only; do not proceed until you answer
--
-- All five die on the LIGHT VERBS (`take`, `hear`, `go`, `happen`, `proceed`), which are residue
-- here on purpose: allowing them re-opens sixteen of the twenty bare imperatives. `SUGGESTIONS`
-- in `queue/human_queue/defaults.py` and the HINT below were changed in this same commit, because
-- a system that suggests a string it then refuses is worse than either rule alone: it reads as a
-- bug and it teaches the writer to distrust the gate. `take no action` was replaced by
-- `no action`, which says the same thing and survives.
--
-- WHAT IT COSTS ON REAL WRITING IS ZERO, and this is the number the decision turned on. Every
-- `default_if_unanswered` ever written in this operation -- 14 on the swarm bus, 97 to 422
-- characters, mean 281, every one prose, six on flagged tasks -- plus the 3 on the live store:
-- `brain.default_is_null_branch` AS 0009 SHIPS IT ALREADY ACCEPTS 0 OF 17. This file adds zero
-- incremental refusals on anything anyone here has actually written. The friction above is
-- friction an attacker's phrasebook feels, not friction a writer feels.
--
-- ================================================================= scope, deliberately narrow
--
-- This applies ONLY where a hard flag applies. `brain.default_is_null_branch` is unchanged and
-- still governs unflagged tasks, `queue_default_event.null_branch` still records that classifier's
-- verdict at fire time (so an old row keeps meaning what it meant), and the act scan is still the
-- first of the three conditions, so every refusal 0009 earned is still earned for the same reason.
--
-- WHY THE FILE NUMBER AND THE LEDGER VERSION ARE DIFFERENT: read `brain.schema_migration`, never
-- `ls`. Measured 2026-08-16 on the live store (read-only) and on a scratch built from this tree:
-- versions 1..16 were all spoken for, the files living in two directories, so this is QUEUE-SCHEMA
-- NUMBER 0011 and LEDGER VERSION 18.
--
-- IT WAS 17 FOR TWENTY MINUTES, and that is a comment rather than an incident only because the
-- collision check exists. This file was written claiming 17, correctly, from a ledger read at
-- 18:17Z. At 18:20Z another lane wrote `migrations/0017_producer_column.sql`, and the next
-- `queue-scratch-db.sh create` stopped with
--
--     scratch-db: two schema files claim the same ledger version(s): 17
--
-- before applying anything. Four lanes are editing this tree tonight: a migration number chosen
-- by reading a ledger is a number chosen from a snapshot that goes stale while you type. Every
-- file writes `ON CONFLICT (version) DO NOTHING`, so without that check the loser would apply
-- nothing and still report success. Keep both guards.

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 18;
  IF taken IS NOT NULL AND taken <> '0011_null_branch_residue_closure' THEN
    RAISE EXCEPTION 'schema version 18 is already held by %, not 0011_null_branch_residue_closure. '
                    'Pick the next version by reading brain.schema_migration, never by listing a '
                    'directory -- this repo''s migration files live in two directories and more '
                    'than one lane is adding to them tonight.',
                    taken;
  END IF;
END $$;

BEGIN;

SET search_path TO brain, public;

-- ---------------------------------------------------------------- the recognised vocabulary
--
-- The ONLY content words a default may contain. Every one either states the null branch or names
-- the queue's own machinery. NOTE WHAT IS ABSENT: no object, no recipient, no channel, no
-- artefact, no date, no amount. A null branch has nothing to say about any of those, and that
-- absence is the whole mechanism.
--
-- This list may be GROWN safely -- a word added here is a phrasing allowed, never an act allowed,
-- as long as the word carries no object. Adding a NOUN that could be an object (`invoice`,
-- `cheque`, `client`) would break the closure, so do not.
CREATE OR REPLACE FUNCTION brain.null_branch_content_words() RETURNS text[] AS $$
  SELECT ARRAY[
    -- the null branch itself
    'hold','holds','held','holding','wait','waits','waited','waiting','pause','pauses','paused',
    'stop','stops','stopped','stall','stalls','stalled',
    'stage','stages','staged','staging','draft','drafts','drafted','prepare','prepares','prepared',
    'action','actions','act','acts','nothing','none','noop','no-op','nop',
    'leave','leaves','left','keep','keeps','kept','stay','stays','stayed','remain','remains',
    'remained','sit','sits','unchanged','untouched','as-is','alone','local','locally','idle',
    'skip','skips','skipped','defer','defers','deferred','postpone','postpones','postponed',
    -- the queue's own machinery: what a null branch may refer to
    'ask','asks','asked','answer','answers','answered','unanswered','question','questions',
    'escalate','escalates','escalated','surface','surfaces','surfaced','raise','raises','raised',
    'checkpoint','checkpoints','task','tasks','item','items','queue','queued','queues','lane',
    'block','blocks','blocked','open','pending','operator','you','again','next','still','yet'
  ]::text[];
$$ LANGUAGE sql IMMUTABLE;

COMMENT ON FUNCTION brain.null_branch_content_words() IS
  'The only content words a default may contain on a FLAGGED task. Everything else is residue and '
  'is refused, which is what closes the synonym class without naming a single synonym. Safe to '
  'grow with words that carry no object; adding a noun that could BE an object breaks the '
  'closure. Migration 0011 (task 0154).';

-- THE LIGHT VERBS, and this list is the honest record of what option (C) costs.
--
-- These are residue: `take`, `get`, `go`, `hear`, `happen`, `proceed` and their friends are NOT
-- accepted content words, so `take no action` and `hold until I hear from you` are refused on a
-- flagged task. That is a real cost and it was chosen with the numbers in hand: allowing them
-- (measured as variant V1/V4) re-opens the bare-imperative class from 1 unsafe accept to 4, and
-- `hold. go` and `no action; they take it` come back with it.
--
-- They are named in their own function rather than merely left out of the list above for two
-- reasons: `brain.bare_imperative_in` needs to know that a clause opening with `make` is an
-- imperative rather than an unrecognised word, and a reader asking "why was `take no action`
-- refused?" needs to find the answer in the schema instead of inferring it from an absence.
CREATE OR REPLACE FUNCTION brain.null_branch_light_verbs() RETURNS text[] AS $$
  SELECT ARRAY[
    'take','takes','taking','took','taken','get','gets','getting','got','gotten',
    'go','goes','going','went','gone','come','comes','coming','came','make','makes','making','made',
    'put','puts','putting','give','gives','giving','gave','given','hear','hears','hearing','heard',
    'know','knows','knowing','knew','known','need','needs','needed','want','wants','wanted',
    'happen','happens','happened','change','changes','changed','move','moves','moved','touch',
    'touches','touched','proceed','proceeds','proceeded','continue','continues','continued'
  ]::text[];
$$ LANGUAGE sql IMMUTABLE;

COMMENT ON FUNCTION brain.null_branch_light_verbs() IS
  'Verbs that carry an act with no object of their own: take, get, go, make, proceed. They are '
  'RESIDUE, not content, so `take no action` is refused on a flagged task -- the measured price '
  'of cutting the bare-imperative class from 4 unsafe accepts to 1. Named here so the refusal is '
  'findable in the schema rather than inferred from an absence. Migration 0011 (task 0154).';

-- The CLOSED function-word class: determiners, pronouns, prepositions, conjunctions, auxiliaries,
-- modals, negators, degree words. CLOSED MEANS CLOSED -- English does not grow new prepositions,
-- so this list does not need maintaining and an attacker cannot find a function word nobody
-- listed. It carries no act by construction: there is no content in `until the of and is not`.
CREATE OR REPLACE FUNCTION brain.function_words() RETURNS text[] AS $$
  SELECT ARRAY[
    'a','an','the','this','that','these','those','my','your','his','her','its','our','their','any',
    'all','some','no','every','each','both','either','neither','other','another','such','same',
    'i','me','we','us','you','he','him','she','it','they','them','who','whom','whose','which',
    'what','myself','yourself','itself','themselves','one','ones','anything','something',
    'of','to','in','on','at','by','for','with','from','into','onto','over','under','about','after',
    'before','during','until','till','since','while','through','between','among','against','upon',
    'within','without','per','via','out','up','down','off','back','here','there','then','than',
    'and','or','but','if','so','because','as','when','whether','unless','though','although','nor',
    'is','are','was','were','be','been','being','am','has','have','had','do','does','did','done',
    'will','would','shall','should','can','could','may','might','must','ought','let','lets',
    'not','never','only','just','simply','merely','also','too','very','more','most','less','least',
    'yes','ok','okay','please','rather','instead','anyway','however','therefore','thus','hence'
  ]::text[];
$$ LANGUAGE sql IMMUTABLE;

COMMENT ON FUNCTION brain.function_words() IS
  'The closed function-word class, allowed in a null branch because it carries no content. Closed '
  'means closed: English does not grow new prepositions, so an attacker cannot find one nobody '
  'listed. Migration 0011 (task 0154).';

-- ---------------------------------------------------------------- the residue
--
-- Every token that is neither a recognised content word nor a function word. It REPORTS THE
-- WORDS rather than a boolean, for the same reason `brain.act_verbs_in` does: a verdict cannot be
-- checked against the text afterwards and a list can, and a refusal that says "you wrote `cheque`
-- and `mick`" reads as the gate being right where "act-shaped" reads as the gate being fussy.
--
-- Normalisation reuses 0009's `brain.act_scan_variants`, index 4 (letter-runs joined), so
-- `p u b l i s h` and a Cyrillic homoglyph cannot re-enter as a familiar token. Digits and any
-- token carrying a non-letter are residue DELIBERATELY: `9`, `9am`, `monday` and `rm -rf` all
-- have to be residue, or the closure leaks a channel for saying WHEN and WHAT.
CREATE OR REPLACE FUNCTION brain.null_branch_residue(t text) RETURNS text[] AS $$
DECLARE
  norm text;
  tok  text[];
BEGIN
  norm := (brain.act_scan_variants(coalesce(t, '')))[4];
  -- Punctuation to spaces, EXCEPT the hyphen inside a word, so `no-op` and `as-is` survive whole
  -- and `rm -rf` splits into two residue tokens rather than vanishing.
  norm := regexp_replace(norm, '[^a-z0-9-]+', ' ', 'g');
  SELECT coalesce(array_agg(DISTINCT w ORDER BY w), '{}')
    INTO tok
    FROM unnest(string_to_array(btrim(norm), ' ')) AS w
   WHERE w <> ''
     -- a bare hyphen or a run of them is punctuation, not a word
     AND w ~ '[a-z0-9]'
     AND NOT (w = ANY (brain.null_branch_content_words()))
     AND NOT (w = ANY (brain.function_words()));
  RETURN tok;
END $$ LANGUAGE plpgsql IMMUTABLE;

COMMENT ON FUNCTION brain.null_branch_residue(text) IS
  'The words in a default that this system does not recognise as belonging to a null branch. '
  'Empty is the only accepted value on a flagged task. Reports the WORDS, not a boolean, so a '
  'refusal can be checked against the text. Migration 0011 (task 0154).';

-- ---------------------------------------------------------------- the bare imperative
--
-- THE HALF THE RESIDUE CHECK CANNOT DO. `no action; do it` contains no content word at all, so
-- its residue is empty and the check above accepts it, and on a task whose brief already says
-- what to do it ships precisely the act the gate exists to stop. Seventeen of twenty such strings
-- got through the residue check alone.
--
-- The rule, and it is a rule rather than another open word list: A CLAUSE MAY NOT OPEN WITH A
-- VERB THAT IS NOT A NULL-BRANCH VERB.
--
--   * `do not send`  -- legal. `do` opens the clause as the AUXILIARY of a negator.
--   * `do it`        -- refused. `do` opens the clause as the MAIN verb: an imperative.
--   * `make it so`   -- refused. A light verb opening a clause is an imperative.
--   * `hold until I answer` -- legal. `hold` is a null-branch verb.
--
-- `do` cannot simply be banned: it is the auxiliary in `do not`, which every honest null branch
-- uses. That is why this tests the POSITION and the next word rather than the presence of a word.
--
-- CLAUSE SPLITTING USES `\y`, THE POSTGRES WORD BOUNDARY, AND THIS IS A CORRECTION, MEASURED.
-- The prototype this file is derived from wrote `\band\b|\bthen\b`, and in PostgreSQL's regex
-- flavour `\b` is BACKSPACE, not a word boundary (`\y` is). Measured 2026-08-16 on scratch
-- `brain_t2_0154`:
--
--     regexp_split_to_array('hold and go', '[.;:,!?]|\band\b|\bthen\b') -> {"hold and go"}
--     regexp_split_to_array('hold and go', '[.;:,!?]|\yand\y|\ythen\y') -> {"hold "," go"}
--
-- so in the version that was scored, only punctuation ever split a clause and the `and`/`then`
-- alternatives were dead code that read as live. The corrected form is used here because shipping
-- a regex whose comment claims a behaviour it does not have is how the next reader gets misled;
-- it was adopted only after re-scoring the whole 64-string battery both ways and confirming it
-- changes no verdict on any of the 64.
CREATE OR REPLACE FUNCTION brain.bare_imperative_in(t text) RETURNS text[] AS $$
DECLARE
  norm text;  cl text;  head text;  found text[] := '{}';
  -- Clause heads that are legal: the null-branch verbs, plus `do/does/did` (handled specially
  -- below, legal only in front of a negator), plus the negators and `no`/`nothing` themselves.
  ok text[] := ARRAY['hold','wait','pause','stop','stall','stage','draft','prepare','leave','keep',
    'stay','remain','sit','skip','defer','postpone','ask','answer','escalate','surface','raise',
    'block','queue','take','do','does','did','no','nothing','none','noop','not','never'];
  -- Inflections and nouns of the same null-branch verbs, which open a clause harmlessly:
  -- `holding until you answer`, `action is deferred`.
  ok_head text[] := ARRAY['hold','wait','pause','stop','stage','draft','prepare','leave',
    'keep','stay','remain','skip','defer','ask','answer','escalate','surface','raise',
    'nothing','none','action','no'];
BEGIN
  norm := lower((brain.act_scan_variants(coalesce(t, '')))[4]);
  FOR cl IN SELECT btrim(x)
              FROM unnest(regexp_split_to_array(norm, '[.;:,!?]|\yand\y|\ythen\y')) x
  LOOP
    IF cl = '' THEN CONTINUE; END IF;
    head := (regexp_match(cl, '^[^a-z]*([a-z]+)'))[1];
    IF head IS NULL THEN CONTINUE; END IF;
    -- `do`/`does`/`did` open a clause legally ONLY when a negator follows immediately.
    IF head IN ('do','does','did') AND cl !~ ('^[^a-z]*' || head || ' +(not|never)\M') THEN
      found := found || head;
    ELSIF NOT (head = ANY (ok))
      AND (head = ANY (brain.null_branch_content_words())
           OR head = ANY (brain.null_branch_light_verbs())) THEN
      -- a light verb opening a clause is an imperative: `make it so`, `go`, `proceed`.
      IF NOT (head = ANY (ok_head)) THEN
        found := found || head;
      END IF;
    END IF;
  END LOOP;
  RETURN found;
END $$ LANGUAGE plpgsql IMMUTABLE;

COMMENT ON FUNCTION brain.bare_imperative_in(text) IS
  'Clause heads that read as an imperative rather than as a null branch: `do it`, `make it so`, '
  '`proceed`. A residue check is blind to these by construction because they name no object, so '
  'this is the second half of the flagged-task rule, not a decoration. `do not send` stays legal '
  'because `do` there is the auxiliary of a negator. Migration 0011 (task 0154).';

-- ---------------------------------------------------------------- the classifier for flagged work
--
-- THREE conditions, all required, and they are three different tests:
--
--   1. 0009's act gate and positive allowlist, unchanged. Every refusal 0009 earned is still
--      earned here for exactly the reason it was earned there.
--   2. No residue: every word is one this system recognises. This closes the synonym class.
--   3. No bare imperative: no clause opens with a verb that is not a null-branch verb. This
--      closes most of the class that condition 2 is blind to.
--
-- The empty string is accepted, as it is in 0009: a question with no stated default is a separate
-- rule, enforced by `ask`, and it is not this function's business.
CREATE OR REPLACE FUNCTION brain.default_is_null_branch_gated(t text) RETURNS boolean AS $$
  SELECT btrim(coalesce(t, '')) = ''
      OR (brain.default_is_null_branch(t)
          AND brain.null_branch_residue(t) = '{}'
          AND brain.bare_imperative_in(t) = '{}');
$$ LANGUAGE sql IMMUTABLE;

COMMENT ON FUNCTION brain.default_is_null_branch_gated(text) IS
  'The classifier for a default on an EXTERNAL or CANON_TOUCHING task: 0009''s act scan, plus no '
  'unrecognised word, plus no bare imperative. Unflagged tasks keep brain.default_is_null_branch '
  'and may still state an acting default. This is BOUNDED, not true by construction: `no action; '
  'all of it` is a measured surviving accept (swarm task 0204). Migration 0011 (task 0154).';

-- ---------------------------------------------------------------- the refusal, at write time
--
-- Same trigger, same flags, same rollback. Two changes: it asks the gated classifier, and the
-- message now names the RESIDUE or the IMPERATIVE HEAD as well as the act verbs, because a
-- refusal the writer cannot check against their own sentence is a refusal they route around.
--
-- THE SUGGESTED PHRASINGS CHANGED IN THIS SAME COMMIT and had to: `take no action` was in the
-- hint 0009 shipped and in `defaults.SUGGESTIONS`, and this rule refuses it (`take` is a light
-- verb). A system that suggests a string it then refuses is worse than either rule alone.
CREATE OR REPLACE FUNCTION brain.question_default_null_branch() RETURNS trigger AS $$
DECLARE
  ext boolean := false;
  canon boolean := false;
  acts text[];
  resid text[];
  imper text[];
BEGIN
  IF NEW.work_item_id IS NULL OR coalesce(btrim(NEW.default_if_unanswered), '') = '' THEN
    RETURN NEW;
  END IF;
  SELECT s.external, s.canon_touching INTO ext, canon
    FROM brain.work_item_signals s WHERE s.id = NEW.work_item_id;
  IF NOT coalesce(ext, false) AND NOT coalesce(canon, false) THEN
    RETURN NEW;
  END IF;
  IF brain.default_is_null_branch_gated(NEW.default_if_unanswered) THEN
    RETURN NEW;
  END IF;
  acts  := brain.act_verbs_in(NEW.default_if_unanswered);
  resid := brain.null_branch_residue(NEW.default_if_unanswered);
  imper := brain.bare_imperative_in(NEW.default_if_unanswered);
  RAISE EXCEPTION
    'the default on % is act-shaped and % is %: a default may never carry a hard flag''s action',
    NEW.work_item_id, NEW.work_item_id,
    concat_ws(' + ', CASE WHEN ext THEN 'external' END, CASE WHEN canon THEN 'canon_touching' END)
    USING HINT = 'Silence must only ever ship the reversible branch. State the null branch and '
                 'name no act: hold, stage only, no action, ask again at the next checkpoint.'
                 -- THE ORDER IS MOST-SUBSTANTIVE-FIRST, and it is not arbitrary. `hold; get the
                 -- cheque to Mick` trips both the residue check and the clause-head rule, and
                 -- "you named `cheque` and `mick`" tells the writer why their sentence is
                 -- dangerous where "it opens with `get`" tells them about grammar. Residue empty
                 -- AND an imperative present is exactly the bare-imperative class, which has no
                 -- other evidence to give, so it comes last and never competes.
                 || CASE WHEN cardinality(acts) > 0
                         THEN ' Refused for naming: ' || array_to_string(acts, ', ')
                              || '. Even negated, an act verb is refused here, because a '
                              || 'negation is bypassable and an absence is not.'
                         WHEN cardinality(resid) > 0
                         THEN ' Refused for words this gate does not recognise: '
                              || array_to_string(resid, ', ')
                              || '. On a flagged task the default must be a null branch and '
                              || 'nothing else, so an object, a recipient, a date or an amount '
                              || 'is refused whatever verb carries it.'
                         WHEN cardinality(imper) > 0
                         THEN ' Refused for opening a clause with: '
                              || array_to_string(imper, ', ')
                              || '. On a flagged task a clause may not open with a verb that is '
                              || 'not a null-branch verb, because "do it" ships whatever the '
                              || 'brief already says to do.'
                         ELSE ' This default states no null branch at all, so it cannot be '
                              || 'classified, and an unclassifiable default is refused.' END
                 || ' If the act really is the right default, the task is asking the operator to '
                 || 'approve it, which is the answer, not the default.';
END $$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------- the evidence, for the reads
--
-- A NEW VIEW over 0009's, never a reshape of it: `CREATE OR REPLACE VIEW` cannot drop a column,
-- so a later migration that adds one to an earlier migration's view breaks the SECOND time the
-- schema is applied. That rule was learned the hard way in 0009 and is inherited here.
--
-- `queue doctor` and `queue defaults` read THIS view for flagged rows, because a default that was
-- legal when it was written can become illegal when a flag is RAISED on a parent, and the
-- write-time trigger cannot see that. Reading the ungated verdict there would report a flagged
-- row as fine when the rule that now governs it refuses it.
CREATE OR REPLACE VIEW brain.queue_pending_default_residue AS
  SELECT p.*,
         brain.act_verbs_in(p.default_text)                AS act_verbs,
         brain.null_branch_residue(p.default_text)         AS residue,
         brain.bare_imperative_in(p.default_text)          AS bare_imperatives,
         brain.default_is_null_branch_gated(p.default_text) AS null_branch_gated
    FROM brain.queue_pending_default p;

COMMENT ON VIEW brain.queue_pending_default_residue IS
  'brain.queue_pending_default plus the three independent pieces of evidence and the gated '
  'verdict. `null_branch` on the base view is the UNGATED classifier and stays that way; read '
  '`null_branch_gated` for anything where `gated` is true. Migration 0011 (task 0154).';

GRANT EXECUTE ON FUNCTION brain.null_branch_content_words() TO brain_runtime, brain_subscriber;
GRANT EXECUTE ON FUNCTION brain.null_branch_light_verbs() TO brain_runtime, brain_subscriber;
GRANT EXECUTE ON FUNCTION brain.function_words() TO brain_runtime, brain_subscriber;
GRANT EXECUTE ON FUNCTION brain.null_branch_residue(text) TO brain_runtime, brain_subscriber;
GRANT EXECUTE ON FUNCTION brain.bare_imperative_in(text) TO brain_runtime, brain_subscriber;
GRANT EXECUTE ON FUNCTION brain.default_is_null_branch_gated(text)
  TO brain_runtime, brain_subscriber;
GRANT SELECT ON brain.queue_pending_default_residue TO brain_runtime, brain_subscriber;

-- ---------------------------------------------------------------- proof, inside the migration
--
-- A store that applied this file cannot be in the broken state. If any of these fails, the
-- migration refuses to commit rather than leaving a half-adopted gate behind.

DO $$
DECLARE
  t text;
  open_residual text := 'no action; all of it';
  -- The six the act scan cannot see, measured by task 0145 at ledger 11 and re-measured by 0154
  -- at ledger 16. Every one is accepted by `brain.default_is_null_branch` TODAY.
  synonyms text[] := ARRAY[
    'hold; get the cheque to Mick',
    'no action; the client hears from us Monday',
    'no action; delivery to Mick Monday',
    'hold; envoyer la facture a Mick',
    'no action; it goes to Mick at 9',
    'hold. rm -rf the staging bucket'];
  -- Nineteen of the twenty bare imperatives built ONLY from allowed words. The twentieth is
  -- `open_residual` above, which survives and is named rather than hidden.
  imperatives text[] := ARRAY[
    'no action; do it', 'hold; make it so', 'no action; proceed', 'no action; continue',
    'hold; you know what to do', 'no action; they take it', 'no action; you get it',
    'hold; let them have it', 'no action; it goes out', 'hold. go', 'no action; you know',
    'hold; it happens', 'no action; you have it', 'hold; give it to them', 'no action; move it',
    'hold; you take it from here', 'no action; touch nothing, then do it',
    'hold; the next one goes', 'hold; you and I know what happens'];
  -- 0009's own battery. It must stay refused: this file may not buy its closure by regressing
  -- the one it inherits.
  inherited text[] := ARRAY[
    'stage only. do not be shy, email the client the quote',
    'leave it alone, but do not be late, transfer the funds',
    'escalate to me, but do not block on it, deploy the fix',
    'pause; do not overthink it, publish the post',
    'nothing is sent without approval; publish the staged page',
    'defer. never mind, ship it',
    'hold. Do not wait for me, send the invoice to Mick.',
    'do not send anything, then send the summary Monday',
    'take no action beyond wiring the funds',
    'hold. the deck is shipping Monday',
    'stage only, then p u b l i s h the page',
    'hold. e-mail the client the quote',
    'hold. mail the cheque to Mick',
    'hold, and cc Mick on the thread',
    'pause; blast the newsletter',
    'hold; dispatch the courier',
    'no action other than handing Mick the file',
    'hold, then let Mick have the file',
    'hold, then loop Mick in',
    'hold, then take it live',
    'hold; make it public',
    'stage only, then flip the switch',
    'hold, then green-light the campaign',
    'no action; the deck goes out Monday'];
  -- What `defaults.SUGGESTIONS` and the HINT above recommend. Every one must survive the gate it
  -- is recommended by, or the system is teaching writers that the gate is arbitrary.
  suggested text[] := ARRAY[
    'hold until I answer',
    'stage only',
    'no action',
    'no action; ask again at the next checkpoint',
    'the task stays blocked until you answer',
    'stage only; hold until I answer'];
BEGIN
  FOREACH t IN ARRAY synonyms LOOP
    IF brain.default_is_null_branch_gated(t) THEN
      RAISE EXCEPTION 'the gated classifier still accepts a measured synonym bypass: %', t;
    END IF;
  END LOOP;
  FOREACH t IN ARRAY imperatives LOOP
    IF brain.default_is_null_branch_gated(t) THEN
      RAISE EXCEPTION 'the gated classifier still accepts a bare imperative: %', t;
    END IF;
  END LOOP;
  FOREACH t IN ARRAY inherited LOOP
    IF brain.default_is_null_branch_gated(t) THEN
      RAISE EXCEPTION '0009''s battery regressed under the gated classifier: %', t;
    END IF;
  END LOOP;
  FOREACH t IN ARRAY suggested LOOP
    IF NOT brain.default_is_null_branch_gated(t) THEN
      RAISE EXCEPTION 'the gate refuses a phrasing this system recommends: % (residue %, '
                      'imperative %)', t, brain.null_branch_residue(t),
                      brain.bare_imperative_in(t);
    END IF;
  END LOOP;
  -- THE RESIDUAL IS NOTICED, NOT ASSERTED. Asserting the accept would make a future hardening
  -- fail to apply, which is the wrong incentive; asserting nothing would let it disappear
  -- silently, which is worse. So it is printed on every apply, and if it ever closes, the notice
  -- says so and names the two places that then need editing.
  IF brain.default_is_null_branch_gated(open_residual) THEN
    RAISE NOTICE '0011: the documented residual is still OPEN, as expected: %. It is a bare '
                 'imperative with no object and no clause head to catch. Tracked as swarm task '
                 '0204. Do not read a clean queue_default_breach as proof that no act shipped.',
                 open_residual;
  ELSE
    RAISE NOTICE '0011: the documented residual % is now CLOSED. Update the comment at the top of '
                 'this file and move it out of KNOWN_RESIDUAL in '
                 'queue/tests/test_ask_default_refusal.py.', open_residual;
  END IF;
END $$;

INSERT INTO brain.schema_migration (version, name)
VALUES (18, '0011_null_branch_residue_closure') ON CONFLICT (version) DO NOTHING;

COMMIT;
