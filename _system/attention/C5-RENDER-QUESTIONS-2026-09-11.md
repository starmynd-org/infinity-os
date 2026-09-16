# Three C5 render questions on the Attention surface, as a checklist

Ruling and checklist by `ATTENTION-OS-admiral`, 2026-09-11 03:15Z, packet ATT-9. Source: the three
questions `INFINITY-CLOSURE-admiral` routed to this surface's own C5 cell (its status file, 02:11Z),
each traced to the pass that raised it. Published before any pass; cite by path and SHA. The
surface under test is the runtime Attention page (`web/templates/attention/**`, `web/views/**`) at
a named SHA served on 8300 or a detached worktree, never the frozen c3 static lane.

Law: `web/MUST-NOT-BUILD.md` at `dc9a99d`, item 8 (the score is overruled with a placement
condition: never beside a row, never in the nav; under the table it is a denominator) and the
design guide's two clauses (one causal accent; state never rides colour alone). Item 2 (no score
editing, no drag-to-reorder) for Q3.

## Q1. Reviewloop's completion-shaped figures (from Closure's P4a on `5df2277`, check 8)

`reviewloop` emits `confirmation.fraction`, `confirmed_fraction`, `false_positive_rate` and
`proposals_per_pass` into a report a consumer may render (`core.py:700-762`). The row half of item
8 is enforced by that component; the placement half is this surface's.

Predicate Q1a: none of those four figures, nor any string of the shape `n/m` or a percentage
derived from a report, renders inside a row of the Attention table (`tr[data-queue-row]`) or in the
nav. Predicate Q1b: where the surface renders any of them, it is under the table in the
denominators paragraph (`[data-attention-denominators]`) or the bar, as a count with its
denominator, never as a rank or a score beside a row.

Measurement: on the served page with rows present, count occurrences of each figure name and of
`\d+/\d+` and `%` inside every `tr[data-queue-row]` (expect 0; control: the same patterns under
`[data-attention-denominators]` and the bar, where "7 of 100" is present as words and the count is
at least 1); in the templates, grep the four names (expect 0 today with the control that
`denominator_note` is found). Today's honest state: this surface does not consume reviewloop's
report at all (grep 0, control present), so Q1 is MET BY ABSENCE and the predicate stands for the
day a consumer is wired.

## Q2. State words beside colour (from Closure's P4b on `885db62`, check 13)

The options component emits five state values as words: `kind` (`review`, `approval`),
`completeness.state` (`complete`, `partial`), `signals.reversibility`, `authority_required`,
`impact.status`. Whether the pane paints a colour and drops the word is a render fact.

Predicate Q2a: every element on the Attention page that carries a state colour class
(`a.att-chip.wait`, `.run`, `.rest`, `.stale`, and any element styled with `--wait`, `--run`,
`--rest`, `--dmg`, `--acc`) has non-empty visible text that names the state in words (denominator:
coloured elements; expect coloured-with-empty-text 0, control: the coloured elements count itself).
Predicate Q2b: the colour bound to damage (`--dmg`) is used only where the text names a failure or
a refusal (the `role=alert` refusal element qualifies; a stripe on success does not); count
elements using `--dmg` and read each text. Predicate Q2c: `kind`, completeness and reversibility
appear as words somewhere on the row or its inspector (the inspector's "What it is now",
"Result", and the signals sentence); count rows whose inspector shows all three words present or
"not stated" (denominator: rows).

Measurement: Playwright at 1440x900 and 375x812; `getComputedStyle` colour of each coloured element
compared to the resolved token values read from `:root`; text read with `innerText`.

## Q3. The Rank cell and the Why cell (from Closure's P4d on `b98c15a`, observation O1)

The ingest proposal describes the existing surface: the producer's `why` renders in the Why cell
and the store's own computed tier reason in the Rank cell; a sender-supplied `rank`, `score`,
`points` or `position` is refused at the door (`RANK-IS-NOT-YOURS`), so the render must never take
a rank from the payload.

Predicate Q3a: the Rank cell's text is composed only from the store-derived fields `rank` (the
ranked position), `tier_label` and `explain_rank()` (declared signals only), and the template
reads no payload field for it (grep `inbox.html` for the Rank cell's expressions; expect exactly
those three sources; control: the Why cell reads `item.why`). Predicate Q3b: sorting a column
changes the view and never the working order: the rank numbers on the rows keep their original
values under `?sort=title` and the banner says the working order is unchanged (`view.reordered`);
count rows whose `#n` differs between the unsorted and the sorted page (expect 0 changed values,
control: the order of rows changed). Predicate Q3c: no control on the page edits a rank or reorders
by drag (`draggable`, `data-sort` on rows, `contenteditable` in the Rank cell: expect 0, control:
the two sortable header links exist).

## The measurement block a tester reports

    Q1a figures inside rows            0 of <coloured or numeric matches>, control under the table n
    Q1b denominators under the table   present, text verbatim
    Q2a coloured elements with empty text  0 of <coloured elements>
    Q2b --dmg uses                     n, each text quoted, all failure or refusal
    Q2c rows with the three words      n of <rows>
    Q3a Rank cell sources              the three names, and no other
    Q3b rank values unchanged by sort  0 of <rows> changed, order changed yes/no
    Q3c rank-editing controls          0, control: sortable headers 2

Every figure MEASURED with instrument and UTC; denominators first; a positive control beside every
zero. This is evidence for the Admiral's reviewer and Closure's; nothing here is a clearance.
