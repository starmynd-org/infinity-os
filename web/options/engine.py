"""Prepare evidence for an agent and compile cited, non-executing ITEM/1.0 proposals.

This is not an ingest door, authentication adapter, scheduler, or runtime executor.
No store import, provider call, network request or arbitrary command can occur here.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Callable

from .corpus import SHA, Source, digest, frontmatter

PROMPT_PATH = Path(__file__).with_name("prompt.md")
PROVENANCE_PATH = Path(__file__).with_name("prompt-provenance.json")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}\Z")
MAX_RESPONSE_BYTES = 32_768
ACTIONS = {"draft": "Draft", "audit": "Audit", "compare": "Compare", "scope": "Scope"}


class InvalidResponse(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise InvalidResponse(reason)


def text(value, maximum):
    if not isinstance(value, str) or not 0 < len(value.strip()) <= maximum:
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return not any(ord(c) < 32 and c not in "\n\t" for c in value)


def exact_keys(obj, keys):
    require(isinstance(obj, dict) and set(obj) == set(keys), "unexpected or missing fields")


def unique_json(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def words(value):
    return set(re.findall(r"[a-z0-9]{3,}", value.casefold())) - {
        "the", "and", "for", "with", "that", "this", "from", "what", "how", "should"}


@dataclass
class Corpus:
    """Trusted local snapshot input; rejected inventory records must not enter this set."""
    sources: dict[str, Source]

    def retrieve(self, query: str, limit: int = 6) -> list[Source]:
        if not 1 <= limit <= 8:
            raise ValueError("retrieval limit must be 1..8")
        terms = words(query)
        aliases = defaultdict(set)
        ranked, metadata = [], {}
        for key, source in self.sources.items():
            meta = frontmatter(source.raw.decode("utf-8-sig"))
            metadata[key] = meta
            for alias in [meta.get("id"), PurePosixPath(source.path).stem, *meta.get("aliases", [])]:
                if isinstance(alias, str):
                    aliases[alias].add(key)
            haystack = " ".join([source.path, str(meta.get("summary", "")), str(meta.get("description", ""))])
            overlap = len(terms & words(haystack))
            if overlap:
                ranked.append((-overlap, key))
        seeds = [key for _, key in sorted(ranked)[:max(1, limit - 2)]]
        neighbors = []
        for key in seeds:
            for edge in metadata[key].get("edges", []):
                target = edge["target"].removeprefix("[[").removesuffix("]]" ).split("|")[0]
                matches = aliases.get(target, set())
                # Ambiguous graph edges are not guessed or silently merged.
                if len(matches) == 1:
                    neighbor = next(iter(matches))
                    if neighbor not in seeds and neighbor not in neighbors:
                        neighbors.append(neighbor)
        return [self.sources[key] for key in (seeds + neighbors)[:limit]]


def prepare(question: str, sources: list[Source], *, context: str = "") -> dict:
    if not text(question, 200) or not isinstance(context, str) or len(context) > 2000:
        raise ValueError("question/context missing or too large")
    if len(sources) > 8 or len({s.key for s in sources}) != len(sources):
        raise ValueError("packet permits at most eight sources")
    evidence, graph = [], []
    terms = words(question + " " + context)
    for source in sources:
        lines = source.raw.decode("utf-8-sig").splitlines()
        meta = frontmatter("\n".join(lines))
        start = lines.index("---", 1) + 1 if lines and lines[0] == "---" else 0
        chunks = []
        for offset in range(start, len(lines), 10):
            excerpt = "\n".join(lines[offset:offset + 10])
            if not excerpt.strip() or len(excerpt) > 2000:
                continue
            chunks.append((-len(terms & words(excerpt)), offset, excerpt))
        for _, offset, excerpt in sorted(chunks)[:3]:
            citation = {"repo": ("your-brain" if source.repo_id == "brain" else "infinity-os"),
                        "sha": source.commit, "path": source.path,
                        "lines": f"L{offset + 1}-L{offset + len(excerpt.splitlines())}",
                        "digest": digest(source.raw)}
            eid = "ev-" + digest(json.dumps(citation, sort_keys=True).encode())[:20]
            evidence.append({"evidence_id": eid, "source_key": source.key, "citation": citation,
                             "node_id": meta.get("id"), "trust": "untrusted", "excerpt": excerpt})
        for edge in meta.get("edges", []):
            graph.append({"source": source.key, "relation": edge["relation"], "target": edge["target"],
                          "meaning": "declared relationship, not proof of a running capability"})
    packet = {"question": question, "context": context, "evidence": evidence, "graph": graph}
    packet["packet_sha256"] = digest(json.dumps(packet, sort_keys=True, ensure_ascii=False).encode())
    return packet


def verify_packet(packet: dict):
    """Detect accidental/stale packet mutation. This hash is integrity, not authorization."""
    try:
        actual = digest(json.dumps({k: v for k, v in packet.items() if k != "packet_sha256"},
                                  sort_keys=True, ensure_ascii=False).encode())
        require(actual == packet["packet_sha256"], "evidence packet digest mismatch")
        require(text(packet["question"], 200), "packet question invalid")
        require(isinstance(packet["evidence"], list) and len(packet["evidence"]) <= 24, "packet evidence bound exceeded")
        ids = set()
        for entry in packet["evidence"]:
            citation = entry["citation"]
            require(entry["trust"] == "untrusted" and text(entry["excerpt"], 2000), "packet excerpt invalid")
            require(citation["repo"] in {"your-brain", "infinity-os"}, "unknown citation repository")
            require(isinstance(citation["sha"], str) and SHA.fullmatch(citation["sha"]), "citation is not frozen")
            require(isinstance(citation["digest"], str) and re.fullmatch(r"[0-9a-f]{64}", citation["digest"]), "citation digest invalid")
            path = citation["path"]
            require(isinstance(path, str) and path and not PurePosixPath(path).is_absolute() and
                    not any(p in {"", ".", ".."} for p in path.split("/")) and
                    not any(c in path for c in "\\:#?\r\n\0"), "unsafe citation path")
            require(isinstance(citation["lines"], str) and re.fullmatch(r"L[1-9]\d*-L[1-9]\d*", citation["lines"]), "invalid citation lines")
            start, end = map(int, re.findall(r"\d+", citation["lines"]))
            require(start <= end and end - start < 10, "invalid citation line range")
            expected = "ev-" + digest(json.dumps(citation, sort_keys=True).encode())[:20]
            require(entry["evidence_id"] == expected and expected not in ids, "packet evidence identity mismatch")
            ids.add(expected)
    except (KeyError, TypeError, UnicodeError, RecursionError) as exc:
        raise InvalidResponse("malformed evidence packet") from exc


def validate(raw: str, packet: dict) -> dict:
    """Return proposed/refused or raise InvalidResponse. No bad-response-to-empty fallback."""
    verify_packet(packet)
    require(isinstance(raw, str), "response must be JSON text")
    try:
        require(len(raw.encode("utf-8")) <= MAX_RESPONSE_BYTES, "response too large")
    except UnicodeError as exc:
        raise InvalidResponse("response is not UTF-8") from exc
    try:
        response = json.loads(raw, object_pairs_hook=unique_json,
                              parse_constant=lambda value: (_ for _ in ()).throw(InvalidResponse("nonfinite JSON")))
    except InvalidResponse:
        raise
    except (ValueError, RecursionError) as exc:
        raise InvalidResponse("malformed JSON") from exc
    require(isinstance(response, dict), "response must be an object")
    if response.get("status") == "refused":
        exact_keys(response, {"status", "reason", "missing"})
        require(text(response["reason"], 500), "refusal reason absent")
        require(isinstance(response["missing"], list) and 1 <= len(response["missing"]) <= 8
                and all(text(v, 300) for v in response["missing"]), "refusal must name missing evidence")
        return response
    exact_keys(response, {"status", "options"})
    require(response["status"] == "proposed", "unknown response status")
    require(isinstance(response["options"], list) and 1 <= len(response["options"]) <= 4,
            "proposal requires one to four options; empty is not a refusal")
    evidence = {entry["evidence_id"]: entry for entry in packet["evidence"]}
    seen = set()
    for option in response["options"]:
        exact_keys(option, {"action", "deliverable", "objective", "rationale", "tradeoff", "citations"})
        require(isinstance(option["action"], str) and option["action"] in ACTIONS, "unsupported action")
        for field, maximum in {"deliverable": 60, "objective": 400, "rationale": 500, "tradeoff": 400}.items():
            require(text(option[field], maximum), f"{field} absent or too long")
        signature = (option["action"], option["deliverable"].strip().casefold())
        require(signature not in seen, "duplicate option")
        seen.add(signature)
        require(isinstance(option["citations"], list) and 1 <= len(option["citations"]) <= 8,
                "each option needs one to eight citations")
        cited = set()
        for ref in option["citations"]:
            exact_keys(ref, {"evidence_id", "quote"})
            require(isinstance(ref["evidence_id"], str) and ref["evidence_id"] in evidence,
                    "citation not in the supplied evidence packet")
            require(ref["evidence_id"] not in cited, "duplicate citation")
            cited.add(ref["evidence_id"])
            require(text(ref["quote"], 2000) and ref["quote"] in evidence[ref["evidence_id"]]["excerpt"],
                    "supporting quote is absent from the cited lines")
    return response


def compile_item(response: dict, packet: dict, *, item_id: str, workspace: str,
                 actor_id: str, created_at: str) -> dict:
    # propose() adds provenance to the already-validated agent response.
    response = {k: v for k, v in response.items() if k != "provenance"}
    validate(json.dumps(response), packet)
    if response["status"] != "proposed":
        raise ValueError("refusal has no Attention item")
    if not all(isinstance(v, str) and ID.fullmatch(v) for v in (item_id, workspace, actor_id)):
        raise ValueError("item, workspace and actor need explicit stable ids")
    from datetime import datetime
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", created_at):
        raise ValueError("created_at must be UTC seconds")
    datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    evidence = {e["evidence_id"]: e for e in packet["evidence"]}
    options = []
    for i, option in enumerate(response["options"], 1):
        options.append({"option_id": f"option-{i}",
                        "label": ACTIONS[option["action"]] + " " + option["deliverable"].strip(),
                        "does": "Proposes work to " + option["objective"].strip().rstrip(".") +
                                ". Execution requires a separate authorized workflow.",
                        "kind": "agent-helps", "reversibility": "costly",
                        "inverse": {"exists": False, "why": "This is a proposal only. No executor or inverse is connected; choosing it must not be presented as completed work."},
                        "recommended": False,
                        "citations": [dict(evidence[r["evidence_id"]]["citation"]) for r in option["citations"]]})
    return {"contract_version": "ITEM/1.0", "c01_version": "0.1.0-draft.5",
            "item_id": item_id, "version": 1, "workspace": workspace,
            "producer": {"source": "brain", "actor": {"actor_id": actor_id, "kind": "worker", "workspace": workspace}},
            "created_at": created_at, "title": packet["question"],
            "why": packet["question"], "kind": "proposal", "options": options,
            "signals": {}, "impact": {"status": "unknown"},
            "freshness": {"never_read": True, "why": "Frozen doctrine supports a method; no live state or trigger has been read."},
            "provenance": {"source_label": "Frozen knowledge, skills and workflows", "zone": "UTC",
                           "steps": [{"at": created_at, "what": "Agent proposed alternatives against evidence packet " + packet["packet_sha256"], "by": actor_id}]},
            "readable": True, "external": False, "canon_touching": False,
            "authority_required": "unknown", "surfacing": "human"}


def propose(packet: dict, generate: Callable[[dict], str] | None) -> dict:
    try:
        verify_packet(packet)
    except InvalidResponse as exc:
        return {"status": "invalid", "reason": str(exc)}
    provenance = {"packet_sha256": packet["packet_sha256"],
                  "prompt_sha256": digest(PROMPT_PATH.read_bytes()),
                  "prompt_provenance_sha256": digest(PROVENANCE_PATH.read_bytes())}
    if not packet["evidence"]:
        return {"status": "refused", "reason": "No readable grounding evidence was supplied.",
                "missing": ["Relevant frozen knowledge, skill or workflow evidence"], "provenance": provenance}
    if generate is None:
        return {"status": "unbuilt", "reason": "No agent generator is connected.", "provenance": provenance}
    try:
        raw = generate({"instructions": PROMPT_PATH.read_text(encoding="utf-8"), "data": packet})
    except Exception as exc:
        return {"status": "error", "reason": "Generator failed: " + type(exc).__name__, "provenance": provenance}
    try:
        response = validate(raw, packet)
    except InvalidResponse as exc:
        return {"status": "invalid", "reason": str(exc), "provenance": provenance}
    return response | {"provenance": provenance}
