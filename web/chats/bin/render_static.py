"""Render one Chats page to a standalone HTML file, for a rendered check without a dev server.

    python web/chats/bin/render_static.py <output.html>

**Why this exists and what it is NOT.** The Chats blueprint is unmounted (R16 is stalled), so there
is nothing served to point a browser at, and this folder caps at five dev servers which the Admiral
allocates. **This surface has no external sub-resources at all** -- inline CSS, no script, no
images, no fonts -- **so it renders identically from a file as it would from a route**, which is
exactly the property that makes a static render honest here and would make it dishonest on a page
with relative assets.

**It closes NO acceptance clause.** Clause 5 needs `EventSource`/`WebSocket`/`serviceWorker`/
`manifest` wrapped before any page script runs, on a SERVED route, by a non-author -- that is
`2026-09-09-IOS-term-12`'s and nothing here substitutes for it. This exists to answer one question I
could otherwise only assert: **does the `<details>` fold actually behave like a fold.**

**mutatesState: YES** on a temp queue root, which it deletes.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from web.chats import harness, registry, views                        # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    out_path = sys.argv[1]
    root = tempfile.mkdtemp(prefix="chats-render-")
    try:
        from flask import Flask

        transcript = os.path.join(root, "t.jsonl")
        with open(transcript, "w", encoding="utf-8", newline="\n") as fh:
            short = "a short turn, well under the fold threshold"
            fh.write('{"type":"user","timestamp":"2026-09-09T20:00:00Z","message":'
                     '{"role":"user","content":"' + short + '"}}\n')
            long_body = "\\n".join(f"line {i} of a long assistant turn" for i in range(30))
            fh.write('{"type":"assistant","timestamp":"2026-09-09T20:00:01Z","message":'
                     '{"role":"assistant","content":[{"type":"text","text":"' + long_body + '"}]}}\n')

        sess = harness.HarnessSession(harness="claude-code", session_id="render",
                                      transcript_path=transcript, workdir=root)
        registry.send_registration(declared_name="render fixture", harness="claude-code",
                                   workdir=root, base=root, provenance=sess.as_body())
        agent_id = registry.accept_registrations(root)[0].agent_id

        app = Flask(__name__, template_folder=os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "templates")))
        views.register(app, base=root)
        html = app.test_client().get(f"/chats/{agent_id}").get_data(as_text=True)
        with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(html)
        print(f"wrote {len(html)} bytes to {out_path}")
        print("folds in this render:", html.count("<details"))
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
