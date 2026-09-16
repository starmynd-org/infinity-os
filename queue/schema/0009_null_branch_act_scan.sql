-- 0009_null_branch_act_scan.sql -- the null-branch classifier stops trying to prove a sentence
--                                  is safe and proves it contains no act.
--
-- Task 0145 (FIX 2), child of 0119. Additive and re-runnable: it redefines two functions, adds
-- one column, one trigger, one CHECK and one view, drops nothing and edits no applied file.
-- `0007_queue.sql` stays exactly as it is. An applied migration is history; history is amended
-- by a later migration.
--
-- ================================================================= what was broken
--
-- The rule the whole defaults design rests on:
--
--     A DEFAULT MAY NEVER CARRY A HARD FLAG'S ACTION.
--     Consequence: silence can only ever ship the reversible branch.
--
-- The D9 adversarial pass (outputs/2026-08-16-D9-adversarial/FINDINGS.md, item 2) defeated it.
-- Measured against `brain.default_is_null_branch` as 0007 shipped it, on 2026-08-16:
--
--     t | stage only. do not be shy, email the client the quote
--     t | leave it alone, but do not be late, transfer the funds
--     t | escalate to me, but do not block on it, deploy the fix
--     t | pause; do not overthink it, publish the post
--     t | nothing is sent without approval; publish the staged page
--     t | defer. never mind, ship it
--     t | hold. Do not wait for me, send the invoice to Mick.
--
-- and the first of those FIRED END TO END on an `external` task, with
-- `brain.queue_default_event` recording `external=t null_branch=t` for a default that says
-- *email the client the quote*. Silence shipped an outward act and the accountability record
-- certified it as a null branch, so the safety claim measured clean while the safety property
-- was broken. That is worse than a wrong answer: it is a wrong answer that self-certifies.
--
-- ================================================================= why the old shape failed
--
-- 0007 looked for NULL-BRANCH EVIDENCE and accepted on finding it: a null-branch phrase had to
-- match, and then every act verb had to be excused by a negator or an article in a 34-character
-- window ending at that verb. D6b already found one false accept of this shape while tuning
-- (*"do not send anything, then send the summary Monday"*) and bounded the window at the
-- previous act verb so one negator could not launder two verbs. That fix was correct and
-- insufficient. The window is still a guess about which clause a negator governs, and English
-- gives an attacker unlimited ways to put a negator somewhere else:
--
--     "do not BE SHY, email the client"   -- the negator governs `be`, not `email`
--
-- Every fix of this kind is one more clause away from the next bypass. So this migration stops
-- parsing negation scope. There is no window, no negator table and no article test, because
-- there is nothing left for them to excuse.
--
-- ================================================================= the new rule, in one line
--
--     ON A FLAGGED TASK A DEFAULT IS A NULL BRANCH ONLY IF NO ACT VERB APPEARS IN IT AT ALL.
--
-- The burden is inverted. `stage only` next to `email the client` is not a null branch; it is
-- an act with a reassuring preamble. Ambiguity resolves to REFUSAL, because the cost of a false
-- refusal is the operator answering one question himself and the cost of a false accept is an
-- outward act nobody authorised.
--
-- This is checkable rather than arguable, which the old shape was not. The accepted set now has
-- a machine-verifiable invariant:
--
--     for every text T,  brain.default_is_null_branch(T)  =>  brain.act_verbs_in(T) = '{}'
--
-- No string containing an act verb is accepted, in any position, under any preamble, however
-- negated. `queue/tests/test_ask_default_refusal.py` asserts that invariant directly over the
-- whole case battery, so a future edit that reintroduces an excuse mechanism fails a test rather
-- than passing quietly.
--
-- ================================================================= WHAT THIS COSTS, stated
--
-- Two phrasings that 0007 accepted are now REFUSED, and one of them was the guidance this
-- system printed at people:
--
--     "stage the reply in drafts, do not send"     -> refused (names `send`)
--     "prepare the deck but do not send it"        -> refused (names `send`)
--
-- That is deliberate and it is the whole point: a negation is bypassable and an absence is not.
-- A null branch never needs to name the act it is not doing. The suggestion list in
-- `queue/human_queue/defaults.py` and the HINT below were changed together with this function so
-- the system stops recommending a phrasing it now refuses. The replacements say the same thing
-- and name nothing: `hold until I answer`, `stage only`, `no action; ask again at the next
-- checkpoint`, `the task stays blocked until you answer`.
--
-- Consequence inside the regex, recorded rather than tidied away: several of `null_re`'s own
-- alternatives ("nothing is sent", "nothing is published", ...) are now unreachable, because the
-- participle is itself in the act vocabulary. `null_re` is left BYTE-IDENTICAL to 0007's so the
-- only semantic change in this function is the act gate in front of it, and so a reviewer
-- diffing the two definitions sees one new idea and not two.
--
-- ================================================================= the second half
--
-- `queue_default_event.null_branch` recorded `t` for a default that shipped an act, so the
-- measurement that is supposed to catch this AGREED WITH THE BUG. A metric that cannot disagree
-- with the thing it measures is not a metric.
--
-- So the event row now carries a SECOND, INDEPENDENT field, `shipped_act_verbs`: the act verbs
-- actually present in the text that was actually shipped, extracted by a raw lexical scan that
-- takes no part in the accept/reject decision. `null_branch` is still the classifier's belief at
-- fire time. The two can now contradict each other, and:
--
--   * a BEFORE trigger computes `shipped_act_verbs` from `default_text` on every INSERT and
--     UPDATE, so no caller can under-report it by passing an empty array;
--   * a CHECK constraint refuses any row that claims `null_branch` while carrying an act verb.
--
-- Under 0007's classifier the end-to-end bypass would have hit that CHECK and the whole
-- transition would have rolled back: the default would not have fired at all. The record can no
-- longer certify a lie, which is the property that was missing.
--
-- WHY THE FILE NUMBER AND THE LEDGER VERSION ARE DIFFERENT. Read from `brain.schema_migration`,
-- never from `ls`: this repo's migration files live in two directories and the ledger is the only
-- truth. Measured on the live store `brain` at 2026-08-16 (read-only): versions 1..9 are taken, 3
-- (`0003_budget`) and 9 (`0009_touch_unresolved_component`) having no file under `queue/schema/`.
-- `queue/bin/queue-scratch-db.sh` applies `queue/schema/*` in `ls` order, so this file must sort
-- after `0008_queue_defer_lineage.sql` to win the `CREATE OR REPLACE`; the ledger must separately
-- record the next free version. So: QUEUE-SCHEMA NUMBER 0009, LEDGER VERSION 11.
--
-- IT WAS 10 UNTIL 16:5x ON 2026-08-16, and the guard below is why that is a comment and not an
-- incident. Another lane landed `migrations/0010_subscriber_identity.sql` in the shared working
-- tree while this task was running, `engine/bin/scratch-db.sh` applies `migrations/*` before the
-- queue schema, and the very next `queue-scratch-db.sh create` stopped with
--
--     ERROR: schema version 10 is already held by 0010_subscriber_identity
--
-- rather than writing a second row for a version that already had an owner. Four lanes are
-- editing one tree tonight; a migration number chosen by reading a directory is a number chosen
-- from a snapshot that is already stale. Keep the guard.

DO $$
DECLARE taken text;
BEGIN
  SELECT name INTO taken FROM brain.schema_migration WHERE version = 11;
  IF taken IS NOT NULL AND taken <> '0009_null_branch_act_scan' THEN
    RAISE EXCEPTION 'schema version 11 is already held by %, not 0009_null_branch_act_scan. Pick '
                    'the next version by reading brain.schema_migration, never by listing a '
                    'directory -- this repo''s migration files live in two directories and more '
                    'than one lane is adding to them tonight.',
                    taken;
  END IF;
END $$;

BEGIN;

SET search_path TO brain, public;

-- ---------------------------------------------------------------- the act vocabulary, once
--
-- ONE list, in ONE place, used by BOTH halves of this migration: the classifier that refuses and
-- the record that audits. That is not a copy: they are the same function called twice, which is
-- what lets the two disagree about a VERDICT while never disagreeing about the VOCABULARY.
--
-- Stems are matched with their ordinary inflections (`-s`, `-es`, `-ing`, `-ed`, `-d`) plus the
-- handful of irregular past forms that matter, because "the invoice is sent Monday" carries the
-- same act as "send the invoice Monday". Inflections only ever NARROW what is accepted, which is
-- the safe direction.
--
-- WHAT IS DELIBERATELY NOT IN HERE, and it is a short list worth reading before adding to it:
-- `hold`, `wait`, `pause`, `stage`, `draft`, `prepare`, `keep`, `leave`, `stay`, `remain`,
-- `skip`, `defer`, `escalate`, `surface`, `ask`, `answer`. Every one of those is a null-branch
-- word. Adding any of them to this list would refuse every honest null branch in the system,
-- including the four this migration now recommends. A verb belongs here when it REACHES OUTSIDE
-- the machine or TOUCHES CANON; it does not belong here merely because it is a verb.

-- THE VOCABULARY IS A LIST OF STEMS AND NOTHING ELSE. Every inflected form is generated by rule
-- in `brain.act_verb_forms`, and that is not tidiness: a hand-written suffix group is where the
-- second measured class of bypass lived. 0009's first draft wrote the suffixes by hand as
-- `(?:e?s|ing|ed|d)?`, which silently misses every English verb that drops its `e` before `-ing`
-- and every one that doubles its final consonant. Measured on 2026-08-16 against that draft:
--
--     t | take no action beyond wiring the funds        -- `wire` + `ing` is `wiring`, not `wireing`
--
-- and the same hole covered `sharing`, `deleting`, `merging`, `charging`, `releasing`,
-- `scheduling`, `invoicing`, `shipping`, `dropping`, `committing`, `submitting` and `running`.
-- One suffix rule written once by rule cannot have that shape of gap in one verb and not another.
CREATE OR REPLACE FUNCTION brain.act_verb_stems() RETURNS text[] AS $$
  SELECT ARRAY[
    -- D6b's original vocabulary from 0007, reduced to stems.
    'send','email','reply','publish','deploy','ship','push','commit','merge','pay','charge',
    'refund','delete','drop','cancel','buy','order','sign','submit','upload','post','message',
    'dm','call','text','notify','transfer','spend','provision','launch','release','apply',
    'overwrite','rename','archive','approve','accept','execute','run',
    -- Outward-reaching verbs 0007 had not thought of. Every one of these was a hole: with only
    -- D6b's list, "hold. wire the funds to Mick" passes the vocabulary AND the allowlist.
    'wire','share','tell','inform','invoice','bill','forward','broadcast','announce','tweet',
    'subscribe','unsubscribe','grant','revoke','install','uninstall','truncate','restore','sync',
    'migrate','wipe','remove','purge','disable','enable','terminate','kill','respond','confirm',
    'book','schedule','hire','withdraw','deposit','sell','quote','ping','contact','alert','invite',
    -- MEASURED HOLES, task 0145. Each of these was found by an attack that got through, not by
    -- brainstorming: `hold. mail the cheque to Mick`, `hold, and cc Mick on the thread`,
    -- `pause; blast the newsletter`, `hold; dispatch the courier`,
    -- `no action other than handing Mick the file`, `hold, then let Mick have the file`.
    'mail','cc','bcc','fax','blast','dispatch','courier','hand','give','remit','settle',
    'authorize','authorise','revert','disclose','leak','deliver',
    -- Irregular past and participle forms, which no suffix rule can generate.
    'sent','paid','spent','bought','sold','told','ran','overwrote','overwritten','gave','given',
    'withdrew','withdrawn',
    -- Multi-word acts, matched literally: `brain.act_verb_forms` inflects nothing that contains a
    -- character outside [a-z], so these are written out and the last one is a small regex.
    'reach out','reaches out','reached out','reaching out',
    'hand over','hands over','handed over','handing over',
    'hand off','hands off','handed off','handing off',
    'go out','goes out','going out','went out',
    'roll out','rolls out','rolled out','rolling out',
    'follow up','follows up','followed up','following up',
    -- One family rather than twenty entries: any of these verbs, with an optional object, taking
    -- a thing live or public. `take it live`, `put the page live`, `make it public`, `went live`.
    '(?:go|goes|going|went|make|makes|making|made|put|puts|putting|set|sets|setting|take|takes|'
      || 'taking|took|turn|turns|turning|turned)(?: [a-z]+)? (?:live|public)',
    -- `loop Mick in`, `let Mick and Sue have it`: an act whose object sits inside the verb.
    'loop(?:s|ed|ing)?(?: [a-z]+)* in',
    'let [a-z ]+ have',
    'hit (?:go|send|publish|deploy|ship|post)',
    'flip the switch','green ?-?light(?:s|ed|ing)?'
  ]::text[];
$$ LANGUAGE sql IMMUTABLE;

COMMENT ON FUNCTION brain.act_verb_stems() IS
  'The single act vocabulary, as STEMS. A verb belongs here when it reaches outside the machine '
  'or touches canon, not merely because it is a verb. Inflections are generated by '
  'brain.act_verb_forms, never written here. Migration 0009 (task 0145).';

-- English inflection, by rule. Overgeneration is deliberate and harmless: `sented`, `depositting`
-- and `payed` are not words, and a form that no honest text contains costs nothing while a form
-- this misses is a bypass. Undergeneration is the only failure mode that matters here.
CREATE OR REPLACE FUNCTION brain.act_verb_forms(stem text) RETURNS text[] AS $$
DECLARE
  f text[];
BEGIN
  -- Anything with a space, a bracket or a quantifier is a phrase, and a phrase is matched as it
  -- was written. Inflecting `let [a-z]+ have` by suffix would produce nonsense.
  IF stem ~ '[^a-z]' THEN
    RETURN ARRAY[stem];
  END IF;
  f := ARRAY[stem];
  -- third person / plural
  IF    stem ~ '(s|sh|ch|x|z)$' THEN f := f || (stem || 'es');
  ELSIF stem ~ '[^aeiou]y$'     THEN f := f || (left(stem, -1) || 'ies');
  ELSE                               f := f || (stem || 's');
  END IF;
  -- past and gerund. THE FIRST BRANCH IS THE ONE THAT WAS MISSING: a stem ending in `e` drops it
  -- before `-ing`, so `wire` becomes `wiring` and a hand-written `(?:ing)?` never sees it.
  IF    stem ~ 'e$'         THEN f := f || (stem || 'd') || (left(stem, -1) || 'ing');
  ELSIF stem ~ '[^aeiou]y$' THEN f := f || (left(stem, -1) || 'ied') || (stem || 'ing');
  ELSE                           f := f || (stem || 'ed') || (stem || 'ing');
  END IF;
  -- consonant-vowel-consonant doubles the final consonant: ship/shipping, drop/dropping,
  -- run/running, commit/committing, cancel/cancelling. `email`, `install` and `bill` do not match
  -- the pattern and correctly get no doubled form.
  IF stem ~ '[^aeiou][aeiou][bdglmnprt]$' THEN
    f := f || (stem || right(stem, 1) || 'ing') || (stem || right(stem, 1) || 'ed');
  END IF;
  RETURN f;
END $$ LANGUAGE plpgsql IMMUTABLE;

-- The word boundaries are LOOKAROUNDS, not captured groups, and that is not a style choice. A
-- captured `([^a-z]|$)` at the end is CONSUMED, so in "hold; publish/send" the first match eats
-- the `/` and the scan then needs a boundary before `send` that is no longer there: it reports
-- {publish} and misses {send}. That understates the record for no reason. Measured both ways on
-- PostgreSQL 16.10; the lookaround form returns {publish, send}.
--
-- Longest alternative first, so the scan reports `sending` rather than `send` and the evidence
-- names the word that is actually in the text.
CREATE OR REPLACE FUNCTION brain.act_verb_re() RETURNS text AS $$
  SELECT '(?<![a-z])(' || string_agg(w, '|' ORDER BY length(w) DESC, w) || ')(?![a-z])'
    FROM (SELECT DISTINCT unnest(brain.act_verb_forms(s)) AS w
            FROM unnest(brain.act_verb_stems()) AS s) f;
$$ LANGUAGE sql IMMUTABLE;

COMMENT ON FUNCTION brain.act_verb_re() IS
  'The act vocabulary compiled to one regex from brain.act_verb_stems and brain.act_verb_forms. '
  'Called by brain.act_verbs_in and so by brain.default_is_null_branch, which is what lets the '
  'refusal and the audit disagree about a VERDICT while never disagreeing about the VOCABULARY. '
  'Migration 0009.';

-- ---------------------------------------------------------------- the disguises, folded
--
-- A whole-string scan for a word is defeated by writing the word differently, and this is not
-- hypothetical: measured against 0009's first draft on 2026-08-16,
--
--     t | stage only, then p u b l i s h the page
--
-- was accepted, and `hold. e-mail the client the quote` and `hold. send the invoice` written with
-- a Cyrillic `s` were caught only by an unrelated noun elsewhere in the sentence. So the scan runs
-- over SEVERAL VARIANTS of the text and takes the union. Union is the safe direction by
-- construction: a variant can only ever add a verb to the evidence, never remove one, so no
-- normalisation here can make a text look safer than the raw string already did.
--
--   raw       the text as written, lowercased. Kept, because normalising can destroy a boundary
--             (`hold.send` -> `holdsend`) and the raw scan is what catches that one.
--   folded    compatibility-decomposed (fullwidth, mathematical alphanumerics, ligatures),
--             combining accents stripped, Cyrillic and Greek homoglyphs mapped to their ASCII
--             twins, zero-width and soft-hyphen characters deleted.
--   bare      folded with the separators a splitter puts INSIDE a word deleted: `e-mail`,
--             `s.e.n.d`, `s*e*n*d`.
--   spaced    folded with runs of single letters joined: `p u b l i s h` -> `publish`. Three or
--             more single letters in a row is not a thing honest English does, and this leaves
--             everything else alone.
--   both      spaced then bared, for `p-u-b-l-i-s-h`.
CREATE OR REPLACE FUNCTION brain.act_scan_variants(t text) RETURNS text[] AS $$
DECLARE
  s      text := lower(coalesce(t, ''));
  folded text;
  bare   text;
  spaced text;
  m      text;
BEGIN
  -- The escapes are \uXXXX rather than the characters themselves ON PURPOSE. A homoglyph table
  -- written in homoglyphs is unreadable in a diff and unsurvivable through a pipe that guesses an
  -- encoding, and this file is applied by `psql -f -` over `docker exec`.
  folded := regexp_replace(normalize(s, NFKD), E'[\u0300-\u036f]', '', 'g');  -- combining accents
  folded := translate(
    folded,
    -- Cyrillic a v e k m n o r s t u h  (12)
    E'\u0430\u0432\u0435\u043a\u043c\u043d\u043e\u0440\u0441\u0442\u0443\u0445'
    -- Cyrillic i j s d h l q w v  (9)
    || E'\u0456\u0458\u0455\u0501\u04bb\u04cf\u051b\u051d\u0475'
    -- Greek alpha beta epsilon eta iota kappa mu nu omicron rho tau upsilon chi gamma  (14)
    || E'\u03b1\u03b2\u03b5\u03b7\u03b9\u03ba\u03bc\u03bd\u03bf\u03c1\u03c4\u03c5\u03c7\u03b3'
    -- Greek omega  (1)
    || E'\u03c9'
    -- dotless i, dotless j, script small l  (3)
    || E'\u0131\u0237\u2113',
    'abekmhopctyx'
    || 'ijsdhlqwv'
    || 'abenikmvoptuxy'
    || 'w'
    || 'ijl');
  -- zero-width space, ZWNJ, ZWJ, word joiner, BOM, soft hyphen
  folded := regexp_replace(folded, E'[\u200b\u200c\u200d\u2060\ufeff\u00ad]', '', 'g');
  bare   := regexp_replace(folded, '[-_.''`*~^|]', '', 'g');
  spaced := folded;
  FOR m IN SELECT (regexp_matches(folded, '(?<![a-z])(?:[a-z] ){2,}[a-z](?![a-z])', 'g'))[1]
  LOOP
    spaced := replace(spaced, m, replace(m, ' ', ''));
  END LOOP;
  RETURN ARRAY[s, folded, bare, spaced,
               regexp_replace(spaced, '[-_.''`*~^|]', '', 'g')];
END $$ LANGUAGE plpgsql IMMUTABLE;

COMMENT ON FUNCTION brain.act_scan_variants(text) IS
  'The text as written plus four de-obfuscated variants, scanned as a union so a variant can only '
  'ever ADD evidence. Closes the class of bypass that writes the act verb differently: e-mail, '
  's.e.n.d, p u b l i s h, a Cyrillic or Greek homoglyph, an accent, a fullwidth or mathematical '
  'letter, a zero-width space. Migration 0009 (task 0145).';

-- The independent scan. It reports WHAT it found rather than a boolean, because the boolean is
-- what got this program into trouble: a verdict cannot be checked against the text afterwards
-- and a list can.
CREATE OR REPLACE FUNCTION brain.act_verbs_in(t text) RETURNS text[] AS $$
  SELECT coalesce(array_agg(DISTINCT m[1] ORDER BY m[1]), '{}'::text[])
    FROM unnest(brain.act_scan_variants(t)) AS v,
         LATERAL regexp_matches(v, brain.act_verb_re(), 'g') AS m;
$$ LANGUAGE sql IMMUTABLE;

COMMENT ON FUNCTION brain.act_verbs_in(text) IS
  'Every act verb present in a text, whole string, no window and no negation scope, over the raw '
  'text and its de-obfuscated variants. This takes NO PART in the accept/reject decision: it '
  'exists so brain.queue_default_event can record what was actually shipped independently of what '
  'the classifier believed, and so the two can contradict each other. Migration 0009 (task 0145).';

-- ---------------------------------------------------------------- the classifier, inverted
--
-- Two conditions, both required, and they are not the same test:
--
--   1. NO ACT VERB ANYWHERE. This is the safety property and it is absolute. It is checked
--      first so the answer for an act-bearing string does not depend on anything else.
--   2. A NULL BRANCH IS POSITIVELY STATED. This is D6b's allowlist direction and it is kept
--      unchanged: a default nobody can classify is refused, because the conservative default in
--      this system is always to show the operator rather than to let silence act. Without it,
--      "wire the funds to Mick" would pass on the vocabulary alone the day someone ships an act
--      verb this list has not thought of.
--
-- Condition 2 is why this is still an allowlist rather than the denylist "no act verb" would be
-- on its own. Condition 1 is why no amount of null-branch phrasing can launder an act.

CREATE OR REPLACE FUNCTION brain.default_is_null_branch(t text) RETURNS boolean AS $$
DECLARE
  s text := lower(coalesce(t, ''));
  -- BYTE-IDENTICAL to 0007's null_re. Several alternatives in it are now unreachable (the
  -- `nothing is sent|published|deployed|...` arm names participles that are themselves act
  -- verbs, so the act gate above refuses those strings first). Left as it was on purpose: the
  -- only semantic change in this function is the act gate, and a reviewer should see one new
  -- idea here, not two.
  null_re text := '(^|[^a-z])(hold|holds|held|wait|waits|pause|paused|stage only|staged only|'
                  || 'stage it only|stage (?:it |the )?[a-z ]{0,20}(?:but|and) (?:do not|don''t|'
                  || 'never)|draft only|prepare[a-z]* (?:it )?[a-z ]{0,20}(?:but|and) (?:do not|'
                  || 'don''t|never)|no action|take no action|nothing (?:is |gets |will be )?'
                  || '(?:sent|sends|ship|ships|shipped|published|publishes|deployed|deploys|'
                  || 'spent|spends|charged|committed|pushed|merged|deleted|changed|happens|'
                  || 'moves)|do not|does not|don''t|never|leave (?:it )?(?:as is|alone|'
                  || 'unchanged|untouched)|stays? (?:blocked|queued|open|as is|local)|remains? '
                  || '(?:blocked|open|unsent)|skip|defer|ask again|escalate|surface|keep (?:it )?'
                  || 'local|keep (?:it )?in draft|no-op|noop)([^a-z]|$)';
BEGIN
  IF btrim(s) = '' THEN
    -- No default at all is not this function's business; `ask` requires one separately.
    RETURN true;
  END IF;
  -- 1. THE ACT GATE. Whole string. No window, no position, no excuse. This single line is the
  --    fix: everything the 34-character window and its negator table used to do is gone, and
  --    with it every way to put a negator somewhere it does not govern.
  IF cardinality(brain.act_verbs_in(s)) > 0 THEN
    RETURN false;
  END IF;
  -- 2. The allowlist, unchanged from D6b.
  RETURN s ~ null_re;
END $$ LANGUAGE plpgsql IMMUTABLE;

COMMENT ON FUNCTION brain.default_is_null_branch(text) IS
  'True when a stated default is a null branch. Migration 0009 (task 0145) inverted the burden: '
  'ANY act verb anywhere in the text disqualifies it, regardless of position and regardless of '
  'what else the text says, and a null branch must still be positively stated. The invariant '
  'is: default_is_null_branch(T) => act_verbs_in(T) = ''{}''. The single implementation: Python '
  'calls this, so the two cannot drift.';

-- ---------------------------------------------------------------- the refusal, reworded
--
-- Same trigger, same gate, same rollback. Only the HINT changes, because the old one recommended
-- "prepare but do not send", which this migration now refuses. Guidance that a system refuses is
-- worse than no guidance: it teaches the writer that the gate is arbitrary.

CREATE OR REPLACE FUNCTION brain.question_default_null_branch() RETURNS trigger AS $$
DECLARE
  ext boolean := false;
  canon boolean := false;
  acts text[];
BEGIN
  IF NEW.work_item_id IS NULL OR coalesce(btrim(NEW.default_if_unanswered), '') = '' THEN
    RETURN NEW;
  END IF;
  SELECT s.external, s.canon_touching INTO ext, canon
    FROM brain.work_item_signals s WHERE s.id = NEW.work_item_id;
  IF NOT coalesce(ext, false) AND NOT coalesce(canon, false) THEN
    RETURN NEW;
  END IF;
  IF brain.default_is_null_branch(NEW.default_if_unanswered) THEN
    RETURN NEW;
  END IF;
  acts := brain.act_verbs_in(NEW.default_if_unanswered);
  RAISE EXCEPTION
    'the default on % is act-shaped and % is %: a default may never carry a hard flag''s action',
    NEW.work_item_id, NEW.work_item_id,
    concat_ws(' + ', CASE WHEN ext THEN 'external' END, CASE WHEN canon THEN 'canon_touching' END)
    USING HINT = 'Silence must only ever ship the reversible branch. State the null branch and '
                 'name no act: hold, stage only, take no action, ask again at the next '
                 'checkpoint.'
                 || CASE WHEN cardinality(acts) > 0
                         THEN ' Refused for naming: ' || array_to_string(acts, ', ')
                              || '. Even negated, an act verb is refused here, because a '
                              || 'negation is bypassable and an absence is not.'
                         ELSE ' This default states no null branch at all, so it cannot be '
                              || 'classified, and an unclassifiable default is refused.' END
                 || ' If the act really is the right default, the task is asking the operator to '
                 || 'approve it, which is the answer, not the default.';
END $$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------- the record that can disagree

ALTER TABLE brain.queue_default_event
  ADD COLUMN IF NOT EXISTS shipped_act_verbs text[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN brain.queue_default_event.shipped_act_verbs IS
  'What the shipped text ACTUALLY CONTAINS, from brain.act_verbs_in, computed by a trigger and '
  'not by the caller. `null_branch` beside it is what the CLASSIFIER BELIEVED at fire time. The '
  'two are independent on purpose: on 2026-08-16 a default reading "stage only ... email the '
  'client the quote" fired on an external task and this table recorded null_branch=t, so the '
  'measurement agreed with the bug. A metric that cannot disagree with the thing it measures is '
  'not a metric.';

-- Computed here rather than in the INSERT so that no caller -- not the verb, not a console, not
-- a superuser at a psql prompt -- can record an empty list beside an act-bearing text.
CREATE OR REPLACE FUNCTION brain.queue_default_event_scan() RETURNS trigger AS $$
BEGIN
  NEW.shipped_act_verbs := brain.act_verbs_in(NEW.default_text);
  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS queue_default_event_scan ON brain.queue_default_event;
CREATE TRIGGER queue_default_event_scan
  BEFORE INSERT OR UPDATE ON brain.queue_default_event
  FOR EACH ROW EXECUTE FUNCTION brain.queue_default_event_scan();

-- Backfill before the CHECK, or an existing self-certifying row would make this migration fail
-- to apply instead of exposing the row. Any row that trips the CHECK after this is a REAL breach
-- and the right thing is for the migration to stop.
UPDATE brain.queue_default_event
   SET shipped_act_verbs = brain.act_verbs_in(default_text)
 WHERE shipped_act_verbs = '{}' AND brain.act_verbs_in(default_text) <> '{}';

-- The lie becomes unrepresentable. Under 0007's classifier the end-to-end bypass hit this row
-- with null_branch=t and shipped_act_verbs={email}; with this constraint the INSERT fails, the
-- whole `queue default fire` transaction rolls back, and the default does not fire at all.
ALTER TABLE brain.queue_default_event
  DROP CONSTRAINT IF EXISTS queue_default_event_null_branch_is_actless;
ALTER TABLE brain.queue_default_event
  ADD CONSTRAINT queue_default_event_null_branch_is_actless
  CHECK (NOT (null_branch AND cardinality(shipped_act_verbs) > 0));

-- ---------------------------------------------------------------- the falsifier, independent
--
-- One row per default that shipped an act on a flagged item, decided by the SCAN and not by the
-- classifier's stored verdict. If the classifier is ever wrong again, this view says so on its
-- own evidence.

CREATE OR REPLACE VIEW brain.queue_default_breach AS
  SELECT e.question_id, e.work_item_id, e.producer, e.checkpoint, e.fired_at, e.default_text,
         e.external, e.canon_touching, e.null_branch, e.shipped_act_verbs,
         e.null_branch AS classifier_said_null_branch,
         (e.null_branch AND cardinality(e.shipped_act_verbs) > 0) AS self_certifying
    FROM brain.queue_default_event e
   WHERE e.fired_at IS NOT NULL
     AND (e.external OR e.canon_touching)
     AND (cardinality(e.shipped_act_verbs) > 0 OR e.null_branch = false);

COMMENT ON VIEW brain.queue_default_breach IS
  'Defaults that fired on a FLAGGED item and were not a null branch, by EITHER test: the stored '
  'classifier verdict or an independent scan of the shipped text. `self_certifying` is the case '
  'the D9 pass found, where the record agreed with the bug; the CHECK on queue_default_event '
  'makes it unwritable, and it is surfaced here anyway so its absence is measured rather than '
  'assumed.';

-- Every pending default with the act verbs it names beside the verdict, so the console and
-- `queue doctor` read the evidence and not only the classifier's answer.
--
-- A SEPARATE VIEW, and `brain.queue_pending_default` IS LEFT EXACTLY AS 0007 WROTE IT. The first
-- draft of this file added `act_verbs` to that view with `CREATE OR REPLACE`, which works once
-- and breaks the second time. Measured on 2026-08-16:
--
--     $ queue/bin/queue-scratch-db.sh migrate
--     psql:<stdin>:514: ERROR:  cannot drop columns from view
--
-- `queue-scratch-db.sh` re-applies EVERY file in `queue/schema/` in order, so on the second run
-- 0007 replaces the view with its own eleven columns, `CREATE OR REPLACE VIEW` refuses to drop
-- the twelfth this file had appended, and the whole `migrate` path dies. `create` hid it by
-- dropping the database first, which is exactly the shape of defect that only shows up on
-- somebody else's machine.
--
-- The rule the next migration should inherit: A LATER MIGRATION MAY NOT CHANGE THE SHAPE OF AN
-- EARLIER MIGRATION'S VIEW. It adds its own view over it. That composes, and it re-runs.
CREATE OR REPLACE VIEW brain.queue_pending_default_scan AS
  SELECT p.*, brain.act_verbs_in(p.default_text) AS act_verbs
    FROM brain.queue_pending_default p;

COMMENT ON VIEW brain.queue_pending_default_scan IS
  'brain.queue_pending_default plus act_verbs: the act verbs the pending default actually names, '
  'from the independent scan. Read this where the evidence matters and the base view where it '
  'does not. Migration 0009 (task 0145).';

GRANT EXECUTE ON FUNCTION brain.act_verb_stems() TO brain_runtime, brain_subscriber;
GRANT EXECUTE ON FUNCTION brain.act_verb_forms(text) TO brain_runtime, brain_subscriber;
GRANT EXECUTE ON FUNCTION brain.act_verb_re() TO brain_runtime, brain_subscriber;
GRANT EXECUTE ON FUNCTION brain.act_scan_variants(text) TO brain_runtime, brain_subscriber;
GRANT EXECUTE ON FUNCTION brain.act_verbs_in(text) TO brain_runtime, brain_subscriber;
GRANT SELECT ON brain.queue_default_breach TO brain_runtime, brain_subscriber;
GRANT SELECT ON brain.queue_pending_default_scan TO brain_runtime, brain_subscriber;

-- ---------------------------------------------------------------- proof, inside the migration
--
-- The bypass battery from FINDINGS.md AND the battery this lane invented against its own fix,
-- asserted here rather than only in the test suite, so that a store which applied this file
-- cannot be in the broken state. If any of these still passes, the migration refuses to commit.

DO $$
DECLARE
  bad text;
  t   text;
  bypasses text[] := ARRAY[
    -- FINDINGS.md item 2, verbatim. All seven classified `t` by 0007 on 2026-08-16.
    'stage only. do not be shy, email the client the quote',
    'leave it alone, but do not be late, transfer the funds',
    'escalate to me, but do not block on it, deploy the fix',
    'pause; do not overthink it, publish the post',
    'nothing is sent without approval; publish the staged page',
    'defer. never mind, ship it',
    'hold. Do not wait for me, send the invoice to Mick.',
    -- D6b's original false accept, which 0007 already refused. Kept so the regression is named.
    'do not send anything, then send the summary Monday',
    -- Invented against this fix, task 0145. Each of these was ACCEPTED by an earlier draft of
    -- this same file; each names the class it broke.
    'take no action beyond wiring the funds',            -- e-dropping inflection
    'hold. the deck is shipping Monday',                 -- consonant doubling
    'stage only, then p u b l i s h the page',           -- letter-spacing
    'hold. e-mail the client the quote',                 -- in-word separator
    'hold. mail the cheque to Mick',                     -- vocabulary gap
    'hold, and cc Mick on the thread',
    'pause; blast the newsletter',
    'hold; dispatch the courier',
    'no action other than handing Mick the file',
    'hold, then let Mick have the file',                 -- object inside the verb phrase
    'hold, then loop Mick in',
    'hold, then take it live',
    'hold; make it public',
    'stage only, then flip the switch',
    'hold, then green-light the campaign',
    'no action; the deck goes out Monday'];
  goods text[] := ARRAY[
    'hold until I answer',
    'stage only',
    'no action; ask again at the next checkpoint',
    'the task stays blocked until you answer',
    'take no action'];
BEGIN
  FOREACH t IN ARRAY bypasses LOOP
    IF brain.default_is_null_branch(t) THEN
      RAISE EXCEPTION 'null-branch classifier still accepts a known bypass: %', t;
    END IF;
  END LOOP;
  FOREACH t IN ARRAY goods LOOP
    IF NOT brain.default_is_null_branch(t) THEN
      RAISE EXCEPTION 'null-branch classifier refuses an honest null branch: %', t;
    END IF;
    IF cardinality(brain.act_verbs_in(t)) > 0 THEN
      RAISE EXCEPTION 'an accepted null branch names an act verb, invariant broken: %', t;
    END IF;
  END LOOP;
  SELECT default_text INTO bad FROM brain.queue_default_event
   WHERE null_branch AND cardinality(shipped_act_verbs) > 0 LIMIT 1;
  IF bad IS NOT NULL THEN
    RAISE EXCEPTION 'an existing event row certifies a null branch while carrying an act: %', bad;
  END IF;
END $$;

INSERT INTO brain.schema_migration (version, name)
VALUES (11, '0009_null_branch_act_scan') ON CONFLICT (version) DO NOTHING;

COMMIT;
