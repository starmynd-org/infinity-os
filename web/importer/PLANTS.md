# ESTATE/1.0 refusal plants

`tests/test_importer.py` contains an isolated red-input plant for every stable refusal
code in `IMPORT-CONTRACT.md`:

| refusal | plant |
|---|---|
| `UNKNOWN-PRODUCER` | live/provider producer name |
| `EXPORT-INCOMPLETE` | complete manifest names missing material; declared file absent |
| `PARTIAL-EXPORT` | partial manifest names no gap; admitted partial forces review |
| `COUNT-MISMATCH` | member list and catalog denominators disagree; positive control does not fire |
| `PATH-ESCAPE` | parent traversal |
| `NOT-UTF8` | invalid Markdown byte |
| `DIGEST-MISMATCH` | manifest and bytes disagree |
| `SOURCE-MOVED-DURING-IMPORT` | first and second reads differ |
| `MALFORMED-FRONTMATTER` | unclosed/duplicate top-level or JSON keys, unsafe 4,301-digit scalar, unsupported flow syntax |
| `MALFORMED-JSON` | truncated JSON and unsafe 4,301-digit manifest/workflow number |
| `EMPTY-WORKFLOW` | zero nodes |
| `MISSING-PAIR` | workflow JSON without Markdown |
| `UNKNOWN-ENTITY-TYPE` | Event or non-scalar type promoted without a mapping |
| `MISSING-REQUIRED-FIELD` | entity without stable id |
| `DUPLICATE-ID-CONFLICT` | two members share one stable id |
| `CREDENTIAL-IN-EXPORT` | flat, nested, mapping, quoted, JSON/simple-flow inline, and JSON credential values |
| `UNDECLARED-LOSS` | omitted provider model with no loss record |

Green controls prove an unknown-authority import offers review rather than enable, a
complete authority-free import is decidable, and all generated swarm stages are disabled
and correctly ordered. The mapper also plants the asymmetric identity case: an actor
present in GitHub but absent from Postgres is refused `ACTOR-UNKNOWN`. These plants are
author-run evidence only until `term-11` reruns them independently. Nested ordinary
metadata is retained in `raw_frontmatter`; Markdown `_ref` and exact n8n `{id, name}`
credential-reference shapes are positive controls. A simple unquoted ordinary flow map is
retained, while a more complex unvalidated flow map is a structured refusal.

`2026-09-09-IOS-term-9`
