# Objectives reach Attention: the arm, the gate that narrows it, and the six option fields

DECIDED 2026-09-10 23:3xZ: Andrew accepted D4 as section 1 and section 3 of this file recommend
(fifth arm; the existing decidability gate decides the default view; `accept` and `recommend accept`
join the attention verb set with the arm; no decline; `dismiss` stays absent; the migration goes
through Platform's path, proven on a disposable clone first, applied to `brain` by Andrew alone).
Recorded in `status/ATTENTION-OS-admiral.md` section 4 in the same turn. The text below is
unchanged from the proposal he decided on.

Definition half of packet ATT-5, by `ATTENTION-OS-admiral`, 2026-09-10 (UTC 18:5xZ). PROPOSAL, not a
migration and not a grant. Platform stages the migration file that implements this; Andrew applies
it to `brain`; nothing here runs against `brain`. It adopts term-5's R28 proposal and R35 revision
(`R-ios5/outputs/2026-09-09-IOS-term-5/OBJECTIVE-QUEUE-R28-PROPOSAL.md`,
`OBJECTIVE-QUEUE-R28-MINIMUM-COUPLING.md`, `OBJECTIVE-QUEUE-R35-ELIGIBILITY.md`) and term-2's
`ITEM/1.0` contract (`R-ios2/outputs/2026-09-09-IOS-term-2/ITEM-CONTRACT-2026-09-09.md`), and
re-derives neither. Where it departs from them it says so.

## 1. The volume, with both denominators (all REPORTED unless marked)

- SQL candidates in `brain.queue_open`: 41 today (38 work_item, 3 recommendation, 0 objective;
  term-18, read only on `brain`, 2026-09-09 23:06 to 23:15Z). With a fifth arm over
  `brain.objective WHERE state = 'inbox'`: 41 + 55 = 96, of which 55 objectives (57 percent).
- Rows the gate admits today: of the 41, 6 blocked before the gate, 35 reach it, 8 admitted,
  27 refused (term-18). The default view is the admitted set.
- Objectives the gate would admit on day one: 0 of 55, because admission requires a decidable
  fact (an option, a completed result, an answer or a recommendation decision; MEASURED
  `web/model.py` `_StoreAttentionPort.queue`, the `decidable` column of its metadata query), and
  `brain.queue_item_option.source_type` is CHECKed to `work_item, question, recommendation`
  (MEASURED `queue/schema/0014_queue_item_options.sql:90`), so no objective can carry an option.
- Heartbeats: 11 of 55 inbox objectives matched heartbeat files by basename (term-18); migration
  50 folds an undeclared origin to `human` and only an explicit `machine` is quiet
  (MEASURED `migrations/0050:113-134`). Whether those 11 carry `origin = 'machine'` on `brain` is
  not measured here.

So "objectives reach Attention" splits into two honest numbers: candidates 41 to 96 (counted,
discoverable, refused with a reason), default-view rows 8 to 8 on day one, rising only as
interpretations attach options to objectives. That is the recommendation in `DECISIONS.md` D4 with
its narrowing property named precisely: the existing decidability gate, not a new heuristic.

## 2. The arm (adopted from term-5, R35 revision)

Fifth `UNION ALL` arm on `brain.queue_open`, `CREATE OR REPLACE VIEW`, all 23 existing columns in
their existing positions and types, appended last, four old arms byte-for-byte in meaning:

    SELECT 'objective'::text, o.id::text, o.name, 'intake'::text, o.produced_by,
           'scope'::text, 'Accept objective'::text,
           NULL::integer, o.taken_in_at, o.taken_in_at,
           NULL::boolean, NULL::boolean, NULL::text, NULL::text, NULL::text, NULL::text,
           NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
           NULL::text, brain.impact_magnitude(NULL)
      FROM brain.objective o
     WHERE o.state = 'inbox'

Departure from term-5's original R28 (which used `brain.objective_waiting`, human origin only):
R35 already superseded that to all inbox rows, machine origin included, so the badge's definition
(`objective_waiting`) and Attention's candidate set are two populations with two names, and the
implementation must never present one count as the other. Nothing is fabricated: no priority, no
signals, no work item, no inverse. `primary_verb` is a label; it never supplies decidability.

`KIND_BY_ARM` gains `("objective", "Accept objective"): "objective"`. The inspector allowlist in
`_StoreAttentionPort.queue` (`source not in (...)`) gains `objective`; the metadata query joins
`brain.objective` for the typed identity and reads `has_option` for it exactly as for the other
sources. A row whose numeric text equals a work item id stays one objective row. Those are
`web/model.py` hunks, which are not this seat's file: they are requested from Platform as the
coupled consumer change term-5 mapped (`R28-MINIMUM-COUPLING` table), and the view and those hunks
are one admission.

## 3. The verbs (the act half of the admission)

Two verbs join the attention set WITH the arm, never before it:

- `accept` (store transition at `engine/swarm_engine/transitions.py:1813`, by name, inbox to
  accepted): the triage act on an objective. Receipt: "<name> accepted into the store; its
  proposals stay on its own page". Inverse: none exists in the store (there is no `unaccept` for
  objectives and no `declined` state; `brain.objective.state` is CHECKed to `inbox, accepted`,
  MEASURED `migrations/0001:330`), so the stripe says "no undo: an accepted objective has no
  inverse verb yet" in those words. Adding a decline state is a separate proposal, not smuggled
  here.
- `recommend accept` (already in the Queue allowlist since D7; `dispatch_option` rides it): the act
  on ONE option of an objective's interpretation, which posts the task, knowledge candidate or CRM
  update the option describes. This is how an objective row becomes decidable and then acted on,
  and it is what makes "creating a task never counts as completing it" structural: the receipt is
  the posted task's id, and the objective stays in inbox until the person accepts it.

`dismiss` stays absent (R36). No agent accepts an objective on the person's behalf; `accept` runs
as the console's human login exactly as `accept work` does.

## 4. The six option fields, and the widening that lets an objective carry an option

`ITEM/1.0` section 5 declares nine fields per option. Persisted today on `brain.queue_item_option`
(18 columns, counted by term-5, MEASURED against `0014:86-110`): `label`, `kind`, `recommended`,
`who`, `lane`, `time_est`, `cost_est` and the calibration pair; the plan, counterargument and
proposed task title live on the `brain.recommendation` the FK points at. Nowhere to live today
(grep `citation` over `queue/schema` and `migrations` is 0, positive control `label` found in
`0014`): `option_id`, `does`, `reversibility`, `inverse`, `citations`, `launches`. Item 10's undo
promise and `INVERSE-UNSTATED` are enforced against a store with nowhere to put them.

Proposed, in the SAME migration file as the arm, all nullable so tolerance rule 5 holds (a path
that serves today keeps serving on a store without them; `store/schema.py::has_column` guards
every read and every write says in words which column is absent):

| Column | Type and check | Holds |
|---|---|---|
| `option_id` | `text`, UNIQUE with `(source_type, source_id)` where not null | the option's own id, stable across drafts |
| `does` | `text` | one sentence, what choosing it does, in the person's terms |
| `reversibility` | `text CHECK (reversibility IS NULL OR reversibility IN ('reversible','costly','irreversible'))` | the act's reversibility; the item-level signal stays the floor |
| `inverse` | `jsonb CHECK (inverse IS NULL OR (inverse ? 'exists'))` | `{exists:true, verb, does}` or `{exists:false, why}`; the receipt stripe reads it at answer time |
| `citations` | `jsonb`, array | `{source, revision, excerpt?}` entries; the contemporaneous "source and item revision" link of the Learning packet |
| `launches` | `text CHECK (launches IS NULL OR launches = 'chats')` | the one option kind that opens the Chats pane |

And one CHECK widening: `queue_item_option.source_type IN ('work_item','question',
'recommendation','objective')`. `queue_item`, `queue_defer` and `queue_bump` are NOT widened (term-5:
unsupported controls stay unavailable and server-refused; hiding a button is not enforcement).
`brain.recommendation.subject_type` has no CHECK (MEASURED `migrations/0001:503`), so a proposal
about an objective is representable there today.

Rollback: `DROP COLUMN` for each of the six (they are nullable and nothing served through them
before), the CHECK restored to three values only after every objective-typed option row is gone
(the migration's down half refuses otherwise, in words), and the view body replaced with the
exact `0017` body. Ledger version: NOT allocated here. Platform allocates from the actual ledger
on a clone (`brain` is REPORTED 69, and 69 does not mean 0070 to 0076 are applied; term-5 records
77 as unreserved); filename prefixes are not versions (`0017` records ledger 49).

## 5. The two admission gates, reconciled rather than left to disagree

`web/model.py` admits on a fact (an option EXISTS); `queue/human_queue/tiers.py::tier_inputs`
composes the tier from overlay fields (`recommended_option`, `template_id`, `prepared_context_link`)
and a question's stated default. One row can read "generative" and "#1 ranked" at once because the
first decides admission and the second decides placement. Proposal: keep both, name them on the
row. Admission stays the model's fact; the tier reason already carries `tier_of`'s sentence; the
Attention row shows both sentences under "Why it is here" and "Why here in the order" (the
inspector already has both slots). No unification of the predicates is proposed, because they
answer different questions, and a merged predicate would hide which one refused a row.

## 6. What must be proven on a clone before Platform stages it (adopted from term-5)

Old-arm row multisets identical before and after; view OID, row type, ACLs and dependents
(`brain.queue_open_for(text)`, `migrations/0037`) preserved through up, down and re-apply; planted
objectives of human, NULL and explicit machine origin, an accepted one (absent), one with no
transcript, one whose id text equals a work item id; gate refusal for an objective with no option
and admission for one with an option naming a real act; a duplicate `accept` refused in words; the
six columns read back through `has_column` on a store that lacks them (a served path does not start
failing). Evidence PASS, FAIL, INCONCLUSIVE or NOT-RUN per case, with the SHA and UTC time.

## 6b. Addendum, 2026-09-10 23:30Z, from the synthetic runs (ATT-3 `1aef02e`, ATT-4 `4946857`)

Three measured facts that change the coupled hunk list and the honesty of section 4, appended
rather than folded in so the reader sees what the proposal knew when:

1. `queue recommend --subject-type objective` is refused by a four-value Python allowlist at
   `queue/human_queue/transitions.py:190` (`queue: unknown subject type 'objective'`, MEASURED A4b
   three times), not by a CHECK; `brain.recommendation.subject_type` has none. So the CHECK widening
   in section 4 is necessary but not sufficient: the same admission needs that allowlist widened
   (and any sibling `SOURCE_TYPES` list term-5 mapped in `queue/human_queue/`), which is a `queue/**`
   hunk outside this seat's write set and is requested from Platform with the `web/model.py` hunks.
2. `brain.approval` already has the shape the Learning packet's field 5 (authorization reference)
   asks for, including `grant_seq` as a real foreign key, and `recommend accept` and
   `recommend reject` never write it (MEASURED A4b, read only). The proposal therefore does not add
   an authorization column: it asks that the two verbs write the approval row they already have a
   home for, which is an engine transition change, again Platform's to stage.
3. The day-one linkage number, so nobody quotes a better one: across 8 outcome records and 72 key
   slots, 1 slot is a plain store fact, 27 mixed, 36 file proxies, 8 reasoned absences (A4b). The
   six columns in section 4 plus the approval write above move the destination receipt, the
   interpretation revision and the authorization reference from proxy to store fact; item revision
   and model, tool and config revisions still have no home and remain proxies until a later packet.

## 6c. Amendment, 2026-09-10 23:55Z, three rulings on the proof (ATT-5 SQL half, `7b02631`)

1. `citations` carries a shape check the definition omitted: `CHECK (citations IS NULL OR
   jsonb_typeof(citations) = 'array')`. Section 4's table said "array" and enforced nothing; a
   non-array accepted silently is the class of quiet wrong answer this file exists against.
2. `KIND_BY_ARM` gains two entries, `("objective", "Accept objective")` and `("objective", None)`,
   because the lookup falls back to `(source_type, None)` and then to `review`, which would
   reinterpret an objective as finished work (term-5's named failure). Accepted as built.
3. The metadata join gains `objective_exists` and the objective branch of `decidable` is gated on
   it: measured (proof case 20), the shipped query reads `decidable=True` for an option row naming a
   nonexistent objective. Accepted as built; the ghost is a real shape once options can name
   objectives.

## 6d. Amendment, 2026-09-11 03:05Z, the third coupled hunk (ATT-8 finding, X5 `5ff029c`)

MEASURED by A10 in a throwaway with the arm and the two `web/model.py` hunks applied: every console
page answers 500 at `web/model.py:868`, `card.update(_VERBS[kind])`, because `_VERBS` has no
`objective` key and `queue_view` is reached from `base.html` on every room (controlled three ways
over nine rows: arm present 500, absent 200, present 500; health 200 throughout). So the admission
has a third coupled change in Platform's file, and the apply order in the staging packet must carry
it. Ruling: `queue_view` excludes `source_type = 'objective'` by one explicit predicate. D4 admitted
objectives to Attention; it did not decide what the Queue room offers on an objective card, and a
`_VERBS["objective"]` entry would decide that by accident. Attention's port reads `brain.queue_open`
directly and is unaffected by the exclusion. Alternative, if Platform prefers: a `_VERBS` entry,
which then needs its own ruling on the Queue room's verbs for objectives.

## 7. What this proposal does not do

It does not apply anything to `brain`. It does not build the interpretation step that attaches
options to objectives (that is the Learning linkage packet ATT-4 on synthetic data first). It does
not add a decline state. It does not widen the intake badge's meaning. It does not resolve
bring-your-own identity, which stays an open gap recorded in the status file.
