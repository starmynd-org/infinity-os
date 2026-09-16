"""Brief rendering: the context a fresh headless session receives for one work item.

The load order is not this module's invention. `_system/retrieval-load-order-policy.md`
LOAD-2 fixes it:

  1. the namespace `INDEX.md`      (the router and operating brief)
  2. `canon/agent-load-order.md`   (navigational, per-namespace sequencing)
  3. `canon/core-doctrine.md`      (`core-contract.md` for a Tool Contract namespace,
                                    plus `canon/current-truth.md` when stateful)
  4. the query-class files named by the INDEX for this query
  5. the long tail, one hop, only when the task names it

LOAD-3: canon is the first content, INDEX is the first navigation. LOAD-5: load the
minimal sufficient set, not the maximal available set.

Loading a whole namespace is the failure mode, not the safe default. So this renderer is
budgeted, and the budget is enforced by demotion rather than by silent truncation: a node
that will not fit is reduced to its frontmatter `summary` and named in the brief's own
"Not loaded" section, with the path to reach it. An agent that knows what it is missing
asks; an agent handed a silently trimmed brief answers confidently and wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import frontmatter, tokens
from .index import EntityIndex, Node

DEFAULT_BUDGET_TOKENS = 25_000

# Headers, stage banners and the "Not loaded" section are written after the budget loop.
# Reserving for them is what keeps the rendered total under the number that was asked for.
FRAMING_RESERVE_TOKENS = 400

CANON_OF_RECORD = ("canon/core-doctrine.md", "canon/core-contract.md")
CANON_STATEFUL = "canon/current-truth.md"
CANON_LOAD_ORDER = "canon/agent-load-order.md"

_WIKILINK = re.compile(r"\[\[([^\]|#]+)")
_BACKTICK_PATH = re.compile(r"`([A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:md|sh|py))`")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "is", "it", "this",
    "that", "with", "as", "by", "at", "from", "how", "what", "when", "which", "does",
    "do", "i", "we", "you", "be", "are", "was", "not", "no", "its", "into", "over",
}


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9-]+", (text or "").lower())
            if w not in _STOPWORDS and len(w) > 2}


@dataclass
class Section:
    stage: int
    label: str
    path: str | None
    entity_id: str | None
    body: str
    included_as: str  # "full" | "summary"

    def render(self) -> str:
        head = f"### {self.label}"
        if self.path:
            head += f"\n<!-- {self.path}"
            if self.entity_id:
                head += f" | id: {self.entity_id}"
            head += f" | loaded: {self.included_as} -->"
        return f"{head}\n\n{self.body.rstrip()}\n"


@dataclass
class Brief:
    work_item_id: str
    namespace: str | None
    query: str
    sections: list[Section] = field(default_factory=list)
    demoted: list[dict] = field(default_factory=list)
    omitted: list[dict] = field(default_factory=list)
    unresolved_refs: list[str] = field(default_factory=list)
    budget_tokens: int = DEFAULT_BUDGET_TOKENS

    def text(self) -> str:
        parts = [
            f"# Brief for work item {self.work_item_id}",
            "",
            f"Namespace routed to: `{self.namespace or 'none (no namespace matched)'}`",
            f"Load order: `_system/retrieval-load-order-policy.md` LOAD-2. "
            f"Budget: {self.budget_tokens} tokens.",
            "",
        ]
        stage = None
        for s in self.sections:
            if s.stage != stage:
                stage = s.stage
                parts.append(f"\n## Stage {s.stage}: {STAGE_NAMES[s.stage]}\n")
            parts.append(s.render())
        parts.append(self._not_loaded())
        return "\n".join(parts)

    def _not_loaded(self) -> str:
        """The section that keeps a budgeted brief honest."""
        lines = ["\n## Not loaded\n"]
        if not (self.demoted or self.omitted or self.unresolved_refs):
            lines.append("Everything the load order selected fit inside the budget.\n")
            return "\n".join(lines)
        if self.demoted:
            lines.append("Reduced to summary only (read the path for the full node):\n")
            for d in self.demoted:
                lines.append(f"- `{d['path']}`{' | id: ' + d['entity_id'] if d.get('entity_id') else ''}")
            lines.append("")
        if self.omitted:
            lines.append("Selected by the load order but dropped for budget:\n")
            for d in self.omitted:
                lines.append(f"- `{d['path']}`{' | id: ' + d['entity_id'] if d.get('entity_id') else ''}")
            lines.append("")
        if self.unresolved_refs:
            lines.append("Referenced by the work item and NOT resolvable in this brain:\n")
            for r in self.unresolved_refs:
                lines.append(f"- `{r}` (unresolved: do not assume it exists)")
            lines.append("")
        lines.append(
            "If the answer depends on anything above, read it before answering. "
            "This brief is a rendering, not the truth.\n"
        )
        return "\n".join(lines)

    def stats(self) -> dict:
        t = tokens.count(self.text())
        return {
            "work_item_id": self.work_item_id,
            "namespace": self.namespace,
            "sections": len(self.sections),
            "sections_full": sum(1 for s in self.sections if s.included_as == "full"),
            "sections_summary": sum(1 for s in self.sections if s.included_as == "summary"),
            "demoted": len(self.demoted),
            "omitted": len(self.omitted),
            "unresolved_refs": len(self.unresolved_refs),
            "budget_tokens": self.budget_tokens,
            **t.to_dict(),
        }


STAGE_NAMES = {
    0: "the work item",
    1: "namespace INDEX (the router)",
    2: "canon load order",
    3: "canon of record",
    4: "query-class files",
    5: "named one-hop references",
}


class BriefRenderer:
    def __init__(self, index: EntityIndex):
        self.index = index
        self.brain = index.brain_root

    # ---- namespace routing -----------------------------------------------------

    def namespaces(self) -> list[str]:
        root = self.brain / "knowledge"
        if not root.is_dir():
            return []
        return sorted(d.name for d in root.iterdir() if d.is_dir() and (d / "INDEX.md").exists())

    def route_namespace(self, query: str) -> tuple[str | None, list[tuple[str, int]]]:
        """Pick the namespace whose slug and INDEX best match the query.

        Deliberately simple and deliberately reported: `route_namespace` returns its
        ranking so a caller can see the margin and override. A confident wrong route is
        worse than a visible weak one.
        """
        q = _terms(query)
        scored = []
        for ns in self.namespaces():
            slug_terms = set(ns.split("-"))
            score = 3 * len(q & slug_terms)
            idx_path = self.brain / "knowledge" / ns / "INDEX.md"
            try:
                head = idx_path.read_text(encoding="utf-8", errors="replace")[:6000]
            except OSError:
                head = ""
            score += len(q & _terms(head))
            if score:
                scored.append((ns, score))
        scored.sort(key=lambda kv: (-kv[1], kv[0]))
        return (scored[0][0] if scored else None), scored[:5]

    # ---- query-class selection -------------------------------------------------

    def query_class_refs(self, namespace: str, query: str, max_rows: int = 2) -> list[str]:
        """The refs named by the best-matching `## Query classes` rows of the INDEX."""
        idx_path = self.brain / "knowledge" / namespace / "INDEX.md"
        try:
            text = idx_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        section = _section(text, "Query classes")
        if not section:
            return []
        q = _terms(query)
        rows = []
        for line in section.splitlines():
            if not line.strip() or line.lstrip().startswith("|---"):
                continue
            score = len(q & _terms(line))
            if score:
                rows.append((score, line))
        rows.sort(key=lambda kv: -kv[0])
        refs: list[str] = []
        for _, line in rows[:max_rows]:
            refs.extend(_WIKILINK.findall(line))
            refs.extend(_BACKTICK_PATH.findall(line))
        return _uniq(refs)

    # ---- render ----------------------------------------------------------------

    def _in_namespace(self, namespace: str | None, ref: str) -> str | None:
        """Prefer the routed namespace's own copy of a commonly-repeated filename.

        `canon/agent-load-order.md` and `canon/core-doctrine.md` exist once per namespace.
        A bare `[[agent-load-order]]` written inside an architecture task means *this*
        namespace's copy, and a global resolve can legitimately land on another one --
        exactly what happened on the first render of this brief. Retrieval is scoped
        before it is global; `entity resolve` stays global, because a `produced_by` id is
        not namespace-relative.
        """
        if not namespace:
            return None
        stem = ref.strip().strip("`").rsplit("/", 1)[-1]
        if stem.endswith(".md"):
            stem = stem[:-3]
        for sub in ("canon", "pillars", "concepts", "decisions", "playbooks", "synthesis", ""):
            rel = "/".join(p for p in ("knowledge", namespace, sub, f"{stem}.md") if p)
            if (self.brain / rel).is_file():
                return rel
        return None

    def _take(self, brief: "Brief", candidates: list, stage: int, ref: str) -> None:
        """Select one reference for loading.

        A brief needs a FILE, not an entity id, so a target that resolves to a path but
        carries no `id:` is still loadable -- `_system/` rule files and `INDEX.md` are
        validator-exempt by design and are exactly the kind of thing a work item names.
        `entity resolve` still refuses those, because `produced_by` does need an id. The
        two verbs want different things and say so separately.
        """
        scoped = self._in_namespace(brief.namespace, ref)
        if scoped:
            candidates.append((stage, scoped, scoped))
            return
        r = self.index.resolve(ref)
        if r.path:
            candidates.append((stage, r.path, r.path))
            return
        brief.unresolved_refs.append(
            f"{ref} [{r.status}"
            + (f", {len(r.candidates)} candidates" if r.candidates else "")
            + "]"
        )

    def render(
        self,
        work_item: dict,
        namespace: str | None = None,
        budget_tokens: int = DEFAULT_BUDGET_TOKENS,
        max_query_class_nodes: int = 6,
    ) -> Brief:
        wid = str(work_item.get("id", "unknown"))
        body = work_item.get("brief") or work_item.get("body") or ""
        query = f"{work_item.get('title', '')} {work_item.get('lane', '')} {body}"

        ns = namespace
        routing = []
        if ns is None:
            ns, routing = self.route_namespace(query)

        brief = Brief(work_item_id=wid, namespace=ns, query=query.strip(), budget_tokens=budget_tokens)
        spent = 0

        # Stage 0: the work item. Never trimmed -- an agent without its own task is useless.
        wi_text = _render_work_item(work_item, routing)
        brief.sections.append(Section(0, "Work item", None, None, wi_text, "full"))
        spent += tokens.estimate(wi_text) + FRAMING_RESERVE_TOKENS

        candidates: list[tuple[int, str, str]] = []  # (stage, label, repo-relative path)
        if ns:
            candidates.append((1, f"knowledge/{ns}/INDEX.md", f"knowledge/{ns}/INDEX.md"))
            alo = f"knowledge/{ns}/{CANON_LOAD_ORDER}"
            if (self.brain / alo).exists():
                candidates.append((2, alo, alo))
            for canon in CANON_OF_RECORD:
                p = f"knowledge/{ns}/{canon}"
                if (self.brain / p).exists():
                    candidates.append((3, p, p))
                    break
            stateful = f"knowledge/{ns}/{CANON_STATEFUL}"
            if (self.brain / stateful).exists():
                candidates.append((3, stateful, stateful))

            for ref in self.query_class_refs(ns, query)[:max_query_class_nodes]:
                self._take(brief, candidates, 4, ref)

        # Stage 5: only what the work item itself names. One hop, never a graph walk.
        for ref in _uniq(_WIKILINK.findall(body) + _BACKTICK_PATH.findall(body)):
            self._take(brief, candidates, 5, ref)

        seen: set[str] = set()
        for stage, label, path in candidates:
            if path in seen:
                continue
            seen.add(path)
            node = self.index.by_path.get(path)
            full = _read(self.brain / path)
            if full is None:
                brief.omitted.append({"path": path, "entity_id": node.entity_id if node else None})
                continue
            cost = tokens.estimate(full)
            if spent + cost <= budget_tokens:
                brief.sections.append(
                    Section(stage, label, path, node.entity_id if node else None, full, "full")
                )
                spent += cost
                continue
            # Over budget: demote to the node's own summary rather than cut mid-sentence.
            summary = _summary_of(self.brain / path, node)
            scost = tokens.estimate(summary)
            if summary and spent + scost <= budget_tokens:
                brief.sections.append(
                    Section(stage, label, path, node.entity_id if node else None, summary, "summary")
                )
                spent += scost
                brief.demoted.append({"path": path, "entity_id": node.entity_id if node else None})
            else:
                brief.omitted.append({"path": path, "entity_id": node.entity_id if node else None})

        brief.unresolved_refs = _uniq(brief.unresolved_refs)
        return brief


def _render_work_item(wi: dict, routing: list[tuple[str, int]]) -> str:
    lines = [f"**{wi.get('title', '(untitled)')}**", ""]
    for key in ("id", "lane", "state", "workdir", "depends_on", "parent"):
        if wi.get(key):
            lines.append(f"- `{key}`: {wi[key]}")
    signals = [k for k in ("stakes", "reversibility", "urgency", "dependency_unblocking",
                           "effort", "confidence", "charter_alignment") if wi.get(k)]
    if signals:
        lines.append("- signals: " + ", ".join(f"{k}={wi[k]}" for k in signals))
    for gate in ("external", "canon_touching"):
        if str(wi.get(gate, "")).lower() in ("true", "yes", "1"):
            lines.append(f"- **GATE `{gate}`: true.** This may wake a listener to prepare, "
                         f"never to send, spend, deploy, publish, or touch canon.")
    if routing:
        lines.append("- namespace routing scores: " +
                     ", ".join(f"{ns}={sc}" for ns, sc in routing))
    body = wi.get("brief") or wi.get("body") or ""
    if body:
        lines += ["", body.rstrip()]
    return "\n".join(lines)


def _section(text: str, heading: str) -> str | None:
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().lower().lstrip("# ").startswith(heading.lower()) and line.lstrip().startswith("#"):
            start = i + 1
            level = len(line) - len(line.lstrip("#"))
            break
    if start is None:
        return None
    out = []
    for line in lines[start:]:
        if line.lstrip().startswith("#") and (len(line) - len(line.lstrip("#"))) <= level:
            break
        out.append(line)
    return "\n".join(out)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _summary_of(path: Path, node: Node | None) -> str:
    if node and node.summary:
        return f"_Summary only, node demoted for budget._\n\n{node.summary}"
    fm = frontmatter.read(path)
    s = fm.get("summary")
    if s:
        return f"_Summary only, node demoted for budget._\n\n{s}"
    return ""


def _uniq(items: list[str]) -> list[str]:
    seen, out = set(), []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out
