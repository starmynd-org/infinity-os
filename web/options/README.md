# Grounded options

Author and sole writer: 2026-09-09-IOS-term-7.

This package prepares an agent's evidence, validates its response, and emits offline
ITEM/1.0 draft proposals. A person can inspect the source for each alternative. It has
no provider adapter, ingest call, queue write, executor, scheduler, UI or acceptance path.
Those parts are unbuilt. A missing generator returns `unbuilt`, not an empty success.

## Pipeline and trust boundary

1. `corpus.read_snapshot` reads two explicit full commit SHAs through Git Bash. The
   tree enumeration and blob reads use the same immutable objects. It never follows
   symlinks, reads worktree content, or consults a live store.
2. `corpus.inventory` parses allowed Markdown. Malformed and duplicate-key YAML are
   listed as refused evidence, not valid empty nodes. This is our reader's admission
   policy, not the brain validator or a finding that those files are defective.
3. `engine.Corpus.retrieve` selects a small set by words in path/summary/description
   and follows uniquely resolved declared graph links one hop. Ambiguous links are
   not guessed. Selection scores stay internal and never become row scores or tiers.
   An exact source selection is also supported for a directed agent read.
4. `engine.prepare` selects up to three bounded excerpts per source. It carries
   node ids, declared relationships, frozen blob digests and exact line spans.
   Excerpts and context are untrusted data. A method is not proof of a current trigger.
5. `engine.propose(packet, generate)` hands the versioned prompt and evidence to a
   caller-supplied agent function. The function returns JSON text. No provider is
   connected by this module; network and account authorization remain upstream.
6. `engine.validate` rejects malformed, overlarge or extra-field responses; missing
   acts, empty/five/duplicate options; unsupported citations and fabricated quotes.
   It checks packet integrity and citation shape. It does not prove semantic entailment.
7. `engine.compile_item` emits the contract's per-option citation objects, explicit
   inverse absence, unknown authority and impact, empty signals, and no tier hint,
   recommendation, priority or score. The receiver still owns authentication, audience,
   freshness, size/depth admission and dispatch. The draft workspace/actor ids in the
   example corpus are fixtures, not provisioned identities.

`proposed`, `refused`, `invalid`, `error`, and `unbuilt` are distinct states. A refusal
names the missing evidence. Invalid JSON and a generator failure never enter the
proposer-refusal numerator. An absent or irrelevant corpus can refuse before generation.

## Read and run

Requires Python 3.12+ and PyYAML 6.0.3. The local run used a projectless bootstrap
because both installed Python runtimes lacked YAML; `requirements.txt` is the portable
dependency list. All runtime imports remain inside this namespace.

The CLI at `cli.py` takes full `--brain-sha` and `--runtime-sha`, `--question`, optional
`--context`, optional exact `--source brain:repo/path.md`, and an absolute `--output`.
It prepares the packet without a response. Supply the same arguments plus an absolute
`--response` file, `--item-id`, `--workspace` and `--actor-id` to validate and compile an
offline item. It re-reads the same frozen sources, not a caller-authored evidence packet.
Exit 0 means proposal; 3 explicit refusal; 4 generator unbuilt; 2 invalid/error.

Python API: construct `Corpus` from admitted `Source` records, `prepare` the retrieved
sources, call `propose` with an authorized generator, then `compile_item` on a proposed
result. None of those calls submit the item or choose an option.

## Corpus and citation limits

The brain allowlist is committed Markdown under `knowledge`, `entities`, `workflows`
and `_system`, excluding any `archive`, `support`, or `_examples` component. Runtime
sources are `docs`, `roles`, README, the prohibition file and queue README. No adapter,
session log, secret registry, raw intake or output corpus is included. This makes the
inventory narrower than the earlier wiki and graph counts in the brief. It also means
an output-linkage audit may be proposed, but cannot be declared complete from this corpus.

The edge count is declared frontmatter edge occurrences, not resolved graph edges.
Only nodes carrying both id and type enter the type census. No full-graph coverage claim.
Whole-corpus retrieval quality has not been measured. Keyword selection and fixed
excerpts can omit necessary context; the agent must refuse when the packet is insufficient.

ITEM/1.0 is pinned to term-2's `35cb22b` contract (9c), which names per-option
`citations: [{repo, sha, path, lines, digest}]`. Rendering is unbuilt in term-2's lane.
The human review file constructs GitHub links from the registered private origin; every
source was verified locally from Git. Host reachability and access to unpublished SHAs
have not been checked; no push is authorized. Citation content is portable even when a
specific remote cannot yet serve its SHA.

The detailed rationale and tradeoff remain in the proposal envelope and human review
artifact. ITEM's current option shape has no fields for them; they are not hidden in
extensions and are not silently invented as runtime fields. The `does` field describes
the proposed work and warns that execution requires a separate authorized workflow.

## Invariants and unresolved human work

`web/MUST-NOT-BUILD.md` at `0180a51`: "every row must be decidable" governs every
alternative. "ONLY where he cannot act on it, and NEVER BESIDE A ROW" governs score
placement. "This item does not forbid undo. It forbids a lie about undo" governs the
inverse declaration. No executable affordance may be enabled until a consumer actually
connects the declared act and receipt behavior.

The first ten options are in the seat's output directory, alongside the raw agent
responses and evidence packets. They are five explicit hypothetical decisions over
Andrew's own frozen material. They are not live queue samples or provider-automated
generations. His verbatim reaction is required and remains pending.

`test_options.py` covers the non-rendered integrity/refusal boundary. The
`--plant-ignore-quote` run deliberately bypasses quote validation and must fail its
fabricated-quote case; the ordinary run must pass it. A reviewer must independently
confirm the pinned export. Term-12 owns source-link and receipt rendering once a
consumer exists; a real human keyboard remains required. Nobody here clears this lane.
