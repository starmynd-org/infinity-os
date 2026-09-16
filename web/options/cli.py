"""Offline CLI: frozen corpus -> packet -> caller-produced JSON response -> ITEM draft."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from options.corpus import git, inventory, read_snapshot
from options.engine import Corpus, compile_item, prepare, propose


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brain-sha", required=True)
    parser.add_argument("--runtime-sha", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--context", default="Offline proposal; no live state has been read.")
    parser.add_argument("--source", action="append", default=[], help="Optional exact repo:path evidence selections, at most eight")
    parser.add_argument("--response", type=Path, help="Agent JSON response; absent means prepare only")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--item-id")
    parser.add_argument("--workspace")
    parser.add_argument("--actor-id")
    args = parser.parse_args()
    if not args.output.is_absolute() or (args.response and not args.response.is_absolute()):
        parser.error("file arguments must be absolute")
    sources = []
    for name, repo, sha in [
        ("brain", "C:/Users/you/repos/your-brain", args.brain_sha),
        ("runtime", "C:/Users/you/repos/_scratch-infinity/worktrees/R-l01", args.runtime_sha),
    ]:
        read, _ = read_snapshot(name, repo, sha)
        sources.extend(read)
    measured = inventory(sources)
    allowed = {r["key"] for r in measured["sources"]}
    corpus = Corpus({s.key: s for s in sources if s.key in allowed})
    if args.source:
        if any(key not in corpus.sources for key in args.source):
            parser.error("requested source absent or refused in the frozen corpus")
        selected = [corpus.sources[key] for key in args.source]
    else:
        selected = corpus.retrieve(args.question)
    packet = prepare(args.question, selected, context=args.context)
    result = {"packet": packet, "mode": "offline; not submitted to ingest"}
    if args.response:
        if args.response.stat().st_size > 32768:
            parser.error("response file exceeds bound")
        response = args.response.read_text(encoding="utf-8")
        result["proposal"] = propose(packet, lambda request: response)
        if result["proposal"]["status"] == "proposed":
            if not all((args.item_id, args.workspace, args.actor_id)):
                parser.error("compiling a proposal needs item-id, workspace and actor-id")
            result["item"] = compile_item(result["proposal"], packet, item_id=args.item_id,
                                          workspace=args.workspace, actor_id=args.actor_id,
                                          created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    else:
        result["proposal"] = propose(packet, None)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(result["proposal"]["status"] + ": " + str(args.output))
    return {"proposed": 0, "refused": 3, "unbuilt": 4, "invalid": 2, "error": 2}[result["proposal"]["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
