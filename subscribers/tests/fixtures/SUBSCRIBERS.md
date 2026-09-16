# Subscribers (test fixture)

A copy of the CONTRACT fields of the `operator-paging` entry, and nothing else, so the paging
suites exercise the real declaration parser without reading the operator's private brain. The
suites used to pass only on a host where that brain sat at the listener's hard-coded default path,
and failed 14 of 16 everywhere else (W5-S5 MEASURED under WSL with BRAIN_ROOT=/nonexistent,
2026-09-16). A suite points at this file unless SUBSCRIBERS_DECLARATION names another.

The declaration of record stays in the operator's brain. If its contract fields change, this copy
must change with them, because the parser reads these exact fields.

## operator-paging

- Subscriber: `operator-paging`
- Owning department: `chief-of-staff`
- Action class: `effect`
- Actions: `escalate`
- Interface:
  - Consumes: `question.`, `fabric.subscriber.quarantined`
- Cost class: `low`
- Flagged-event posture: `gated`
- Phase: `1`
- Status: `live`
