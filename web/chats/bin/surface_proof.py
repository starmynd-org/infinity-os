"""THE CHATS SURFACE, RENDERED AND CHECKED STRUCTURALLY. Not a browser measurement, and it says so.

    python web/chats/bin/surface_proof.py

**mutatesState: YES** -- it builds a queue in a temp root and registers agents into it.

WHAT THIS IS AND IS NOT. It renders the templates through Jinja and parses the RESULT as HTML.
**It is not a browser and it closes no acceptance clause.** `SEAT-COMMON` §4 clause 5 --
*"survives the prohibitions, checked by a non-author"* -- needs `EventSource`, `WebSocket`,
`serviceWorker` and `manifest` replaced with instrumented wrappers **before any page script runs**,
then more than two poll cycles on the page. **That is a pane measurement and it is
`2026-09-09-IOS-term-12`'s. Nothing here substitutes for it.**

THE TRAP THIS FILE IS BUILT AROUND, AND IT IS THE ONE MY BRIEF NAMES FOR THIS SURFACE

> *"A page that renders conversations cannot be text-searched for a forbidden construct."*

`term-2`'s own check reported `contenteditable: 3` and **all three were the word in transcript
prose; zero were attributes.** This surface renders THIS FLEET'S transcripts, in which agents
discuss `websocket`, `EventSource` and `contenteditable` by name -- **including the very messages
this seat has been writing all day.** So every prohibition check here **parses the HTML and asks the
DOM for the attribute or the tag**, and the fixture below deliberately plants those words in message
text so that a grep-shaped check would fail and this one does not.

**Read the match, not the count.**
"""

from __future__ import annotations

import html.parser
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from web.chats import history, protocol, registry, views                # noqa: E402

CHECKS = 0
FAILURES: list[str] = []

#: Message text planted into the fixture transcript. **Every forbidden word appears here as PROSE.**
#: A `grep -c contenteditable` over the rendered page must return a non-zero number for this proof
#: to be worth anything: it is what proves the DOM checks are not passing by accident.
POISON = (
    "I nearly filed a finding that the surface never polls. The instrument wrapped fetch and "
    "WebSocket and EventSource before any page script ran, and contenteditable was the attribute "
    "I should have queried for rather than grepped. A service worker and a manifest are both "
    "forbidden with no amendment sought."
)


class Parsed(html.parser.HTMLParser):
    """A DOM-shaped reading of the page. **Attributes and tags, never the text.**"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: dict[str, int] = {}
        self.attrs_seen: dict[str, int] = {}
        self.rel_values: list[str] = []
        self.text_len = 0

    def handle_starttag(self, tag, attrs):
        self.tags[tag] = self.tags.get(tag, 0) + 1
        for name, value in attrs:
            self.attrs_seen[name] = self.attrs_seen.get(name, 0) + 1
            if tag == "link" and name == "rel":
                self.rel_values.append(str(value))

    def handle_data(self, data):
        self.text_len += len(data)


def check(label: str, ok: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  --  {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def main() -> int:
    root = tempfile.mkdtemp(prefix="chats-surface-")
    print("=" * 78)
    print("CHATS SURFACE -- rendered through Jinja, parsed as HTML, checked at the DOM")
    print(f"  as-of        {protocol.utc_stamp()}")
    print("  NOT a browser. Acceptance clause 5 is UNMET and belongs to 2026-09-09-IOS-term-12.")
    print("  mutatesState YES on a temp queue root.  store  NONE OPENED.")
    print("=" * 78)

    try:
        from flask import Flask
    except Exception as exc:                                            # pragma: no cover
        print(f"  flask unavailable: {exc}")
        return 1

    # ---------------------------------------------------------------- fixture
    transcript = os.path.join(root, "fixture-transcript.jsonl")
    with open(transcript, "w", encoding="utf-8", newline="\n") as fh:
        fh.write('{"type":"user","timestamp":"2026-09-09T16:00:00Z","message":{"role":"user",'
                 '"content":"' + POISON.replace('"', "'") + '"}}\n')
        long_body = "\\n".join(f"line {i}" for i in range(40))
        fh.write('{"type":"assistant","timestamp":"2026-09-09T16:00:01Z","message":'
                 '{"role":"assistant","content":[{"type":"text","text":"' + long_body + '"}]}}\n')
        fh.write('{"type":"attachment","timestamp":"2026-09-09T16:00:02Z"}\n')

    from web.chats import harness
    sess = harness.HarnessSession(harness="claude-code", session_id="fixture",
                                  transcript_path=transcript, workdir=root)
    sess.verified = True
    registry.send_registration(declared_name="fixture agent", harness="claude-code",
                               workdir=root, base=root, provenance=sess.as_body())
    minted = registry.accept_registrations(root)
    check("the fixture agent registered with a verified transcript",
          bool(minted) and minted[0].transcript_verified,
          f"agent_id={minted[0].agent_id if minted else None}")
    agent_id = minted[0].agent_id

    app = Flask(__name__, template_folder=os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "templates")))
    # Through `views.register`, which is the SAME entry point `web/app.py` would call, so this
    # proof exercises the mount path rather than a private one. A proof that wires the blueprint
    # by hand would pass over a `register` that was broken.
    views.register(app, base=root)
    client = app.test_client()

    pages = {}
    for label, path in (("index", "/chats/"), ("agent", f"/chats/{agent_id}")):
        resp = client.get(path)
        check(f"{label} renders 200", resp.status_code == 200, f"{path} -> {resp.status_code}")
        pages[label] = resp.get_data(as_text=True)

    # ---------------------------------------------------------------- the poison is really there
    print("\nTHE POISON ANCHOR -- without this the DOM checks below prove nothing")
    body = pages["agent"]
    grep_hits = sum(body.lower().count(w) for w in
                    ("contenteditable", "websocket", "eventsource", "service worker", "manifest"))
    check("a GREP of the rendered page finds forbidden words", grep_hits >= 5,
          f"{grep_hits} textual hits -- a grep-shaped check would call this page a violation")

    # ---------------------------------------------------------------- the DOM checks
    print("\nPROHIBITIONS, ASKED OF THE DOM AND NOT OF THE TEXT")
    for label, text in pages.items():
        dom = Parsed()
        dom.feed(text)
        check(f"{label}: zero <script> elements", dom.tags.get("script", 0) == 0,
              f"{dom.tags.get('script', 0)} script tags; {dom.text_len} chars of text parsed")
        check(f"{label}: zero contenteditable ATTRIBUTES",
              dom.attrs_seen.get("contenteditable", 0) == 0,
              "the word appears in prose; the attribute does not")
        check(f"{label}: zero <form>, <input>, <textarea>, <button>",
              all(dom.tags.get(t, 0) == 0 for t in ("form", "input", "textarea", "button")),
              "nothing on this page can be typed into or submitted")
        check(f"{label}: no link[rel=manifest]",
              not any("manifest" in r.lower() for r in dom.rel_values),
              f"link rel values seen: {dom.rel_values or 'none'}")

    # ---------------------------------------------------------------- the disclosure
    print("\nTHE DISCLOSURE -- present, leading, and not smaller than what it governs")
    for label, text in pages.items():
        check(f"{label}: carries the refusal sentence",
              "You cannot send from here" in text, "1 occurrence required; 0 was the prior state")
        before_notice = text.split("You cannot send from here")[0]
        check(f"{label}: the refusal OPENS the notice rather than trailing it",
              "<b>" in before_notice[-60:] or "notice" in before_notice[-400:],
              "leading with the refusal is what makes a composer-shaped box safe")
    check("the notice is NOT set smaller than the content it governs",
          ".notice { font-size: 1rem" in pages["index"],
          "the prior lane measured its own at 11.2px under 13.28px text and called it a footnote")

    # ---------------------------------------------------------------- item 8 and the fold
    print("\nITEM 8 AND THE FOLD")
    check("no numeric badge sits beside an agent row",
          "inbox_pending" not in pages["index"] and "outbox_pending" not in pages["index"],
          "item 8's placement condition: never beside a row. A DIRECTION is asserted instead.")
    check("a long turn is folded and the fold says how many lines it hides",
          "show all &middot;" in pages["agent"] or "show all ·" in pages["agent"],
          "the fold uses <details>, so there is no script and the browser owns the state")
    window = history.read_window(transcript, offset=0, limit=50)
    # WHITESPACE-NORMALISED, and the reason is that my first version of this check FAILED on a
    # correct page. It tested for the literal `"are not conversation"`, which the template emits
    # across a source line break as `"are not\n      conversation"`. **The page was right and the
    # check was wrong**, and a substring test against HTML is whitespace-sensitive in a way nobody
    # looking at the rendered text would guess. Left as a note because the failure mode is generic:
    # a check like this fails LOUDLY here, but the same mistake inverted -- a substring that
    # accidentally matches -- would have passed silently.
    flat = " ".join(pages["agent"].split())
    check("the page states the denominator and what it is NOT showing",
          f"of {window.total}." in flat and "are not conversation" in flat,
          f"{window.counts.not_rendered} of {window.counts.lines} records not rendered")

    # ---------------------------------------------------------------- paging
    print("\nPAGING -- plain links, no script, and the last page says it is the last")
    # A fixture long enough to page: 130 turns over a 50-turn page.
    long_path = os.path.join(root, "long.jsonl")
    with open(long_path, "w", encoding="utf-8", newline="\n") as fh:
        # EACH TURN IS 15 LINES, NOT ONE, AND THAT IS THE FIXTURE'S WHOLE POINT.
        # The first version wrote one-line turns. `line_count > 12` was therefore false for every
        # one of them, NO fold ever rendered, and the tab-stop check below scored PASS over ZERO
        # folds -- a check passing because the state it examines was unreachable in its own fixture.
        # That is the third time today a green of mine meant "the case never reached the plant".
        for i in range(130):
            body = "\\n".join([f"turn {i}"] + [f"body line {j}" for j in range(14)])
            fh.write('{"type":"user","timestamp":"2026-09-09T16:00:00Z","message":'
                     '{"role":"user","content":"' + body + '"}}\n')
    long_sess = harness.HarnessSession(harness="claude-code", session_id="long",
                                       transcript_path=long_path, workdir=root)
    registry.send_registration(declared_name="long agent", harness="claude-code", workdir=root,
                               base=root, provenance=long_sess.as_body())
    long_id = registry.accept_registrations(root)[0].agent_id

    first = client.get(f"/chats/{long_id}").get_data(as_text=True)
    mid = client.get(f"/chats/{long_id}?from=50").get_data(as_text=True)
    last = client.get(f"/chats/{long_id}?from=100").get_data(as_text=True)
    junk = client.get(f"/chats/{long_id}?from=not-a-number")

    check("page 1 counts from 1 and knows the total",
          "turns 1&ndash;50" in first or "turns 1–50" in first, "denominator printed on the page")
    check("page 1 offers 'older' and not 'newer'",
          "?from=50" in first and "?from=0" not in first.split("pager")[0].split("older")[0],
          "there is nothing before the first page")
    check("a middle page offers BOTH directions",
          "?from=0" in mid and "?from=100" in mid, "newer and older both reachable")
    check("the LAST page says it is the last, rather than looking like a failed load",
          "That is the end of this conversation as recorded" in last,
          "a last page and a broken page are otherwise identical to a reader")
    check("the last page has no 'older' link", 'href="?from=150"' not in last,
          "no link past the end of the file")
    check("a junk offset returns 200 and the first page, not a 500",
          junk.status_code == 200 and ("turns 1&ndash;50" in junk.get_data(as_text=True)
                                       or "turns 1–50" in junk.get_data(as_text=True)),
          "a malformed query string is a person editing a URL")
    pager_dom = Parsed()
    pager_dom.feed(last)
    check("paging added no <script> and no <button>",
          pager_dom.tags.get("script", 0) == 0 and pager_dom.tags.get("button", 0) == 0,
          f"{pager_dom.tags.get('a', 0)} <a> elements; every control is a link")

    # ---------------------------------------------------------------- search
    print("\nSEARCH -- a query parameter, no input element, and a real zero says so")
    hit = client.get(f"/chats/{long_id}?q=turn+7").get_data(as_text=True)
    miss = client.get(f"/chats/{long_id}?q=zzzz-no-such-string").get_data(as_text=True)
    paged = client.get(f"/chats/{long_id}?q=turn&from=50").get_data(as_text=True)
    flat_hit, flat_miss, flat_paged = (" ".join(p.split()) for p in (hit, miss, paged))

    check("a search reports MATCHES OUT OF THE WHOLE FILE, not just its own size",
          "of 130 turns match" in flat_hit,
          "a result that reports only its own size never answers 'out of how many'")
    # THE CASE THAT ACTUALLY REACHES THE DEFECT, and it exists because a plant did not go red.
    # I replaced `window.matched = hits` with `= len(chosen)` -- the exact "reports only its own
    # size" bug -- and all 33 checks stayed green. The check above uses `q=turn 7`, whose ~11
    # matches all fit inside one 50-turn page, so `hits` and `len(chosen)` are EQUAL and the two
    # implementations are indistinguishable. `q=turn` matches all 130 over a page of 50, so they
    # differ by 80. **A planted defect that goes unnoticed does not mean the code is right; it
    # means the case never reached the plant.**
    check("...and it still does when the matches OVERFLOW a page",
          "130 of 130 turns match" in flat_paged,
          "q=turn matches 130 over a page of 50; a size-reporting bug would say 50 here")
    check("a search that matches nothing says it is a REAL zero",
          "That is a real zero, not an error" in flat_miss and "130 turns were read" in flat_miss,
          "an empty result and a broken search are otherwise identical to a reader")
    check("the miss names what search does NOT cover",
          "are not searched" in flat_miss,
          "a search that silently skips records is worse than no search")
    check("paging a search KEEPS the query", "q=turn" in flat_paged and "match" in flat_paged,
          "dropping it would page back into the unfiltered conversation at the same offset")
    check("the page says there is no search box and why",
          "no search box" in flat_hit and "?q=" in flat_hit,
          "a missing affordance that is never explained reads as a defect")

    search_dom = Parsed()
    search_dom.feed(hit)
    check("SEARCH ADDED NO <input>, <form>, <textarea> OR <button>",
          all(search_dom.tags.get(t, 0) == 0
              for t in ("input", "form", "textarea", "button")),
          "the zero-typable-element invariant survives the feature")

    # ---------------------------------------------------------------- tab stops
    #
    # PROMPTED BY A REAL KEYBOARD DRIVE, NOT BY THEORY. Andrew drove `term-2`'s frozen Chats
    # surface at ~194500Z and it took **approximately 30 Tabs to reach a chat** (relayed by
    # `term-13` at `200355Z`). That is a finding about that surface and NOT about this one -- but
    # this surface has a `<details>` fold per turn, and 50 turns a page, so the same defect class is
    # available here in a worse form and nobody had counted.
    #
    # **THIS IS A STRUCTURAL COUNT AND IT IS NOT A KEYBOARD JOURNEY.** `SEAT-COMMON` §4: a
    # programmatic audit *"answers 'is the order sane and is every stop visible'. It does not answer
    # 'does a person tabbing through get a sensible journey'."* Clause 2 stays
    # BLOCKED-ON-A-HUMAN-KEYBOARD and nothing here touches it. Counting is still worth doing:
    # geometry refuses in a 0x0 pane, structure does not.
    print("\nTAB STOPS -- a structural count. NOT a keyboard journey, and it closes no clause.")

    class Focusable(html.parser.HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.stops: list[str] = []

        def handle_starttag(self, tag, attrs):
            attr = dict(attrs)
            # The natively focusable set this surface can actually contain. `<summary>` IS a tab
            # stop and is the one people forget, which is the whole reason for this check.
            if tag == "a" and attr.get("href") is not None:
                self.stops.append("a")
            elif tag in ("summary", "input", "button", "textarea", "select"):
                self.stops.append(tag)
            elif attr.get("tabindex") not in (None, "-1"):
                self.stops.append(f"{tag}[tabindex]")

    for label, text in (("index", pages["index"]), ("agent-page-1", first)):
        f = Focusable()
        f.feed(text)
        kinds: dict[str, int] = {}
        for s in f.stops:
            kinds[s] = kinds.get(s, 0) + 1
        print(f"  {label}: {len(f.stops)} tab stops  {kinds}")

    index_stops = Focusable()
    index_stops.feed(pages["index"])
    agent_stops = Focusable()
    agent_stops.feed(first)

    check("the Chats index is reachable in a handful of stops, not thirty",
          len(index_stops.stops) <= 12,
          f"{len(index_stops.stops)} stops; Andrew hit ~30 Tabs to a chat on the frozen surface")
    check("every tab stop is a link or a fold -- nothing typable is focusable",
          all(s in ("a", "summary") for s in agent_stops.stops),
          f"kinds present: {sorted(set(agent_stops.stops))}")
    # THE CHECK I FIRST WROTE HERE WAS TOO LENIENT TO CATCH THE DEFECT IT WAS AIMED AT.
    # It asserted `folds <= total - 4` and PASSED at 50 folds of 55 stops -- which is precisely the
    # shape of the finding Andrew's keyboard drive produced on the frozen surface, only worse.
    # An assertion that is satisfied by the failing state is not an assertion.
    #
    # What actually fixes a long unskippable run is a SKIP LINK, and what this now checks is that
    # one exists and is FIRST. A skip link that is not the first stop is not a skip link.
    folds = agent_stops.stops.count("summary")
    print(f"    (folds on this page: {folds} of {len(agent_stops.stops)} stops -- the raw number, "
          f"reported whether or not it flatters the page)")
    check("a SKIP LINK exists and is the FIRST tab stop on the page",
          'href="#end-of-page"' in first
          and first.index('href="#end-of-page"') < first.index('href="/chats/"'),
          "without it, reaching the pager means tabbing past every fold")
    check("the skip target actually exists on the page",
          'id="end-of-page"' in first,
          "a skip link to a missing anchor moves focus nowhere and reports nothing")

    # SUMMARY MUST BE THE FIRST CHILD OF ITS DETAILS, AND THIS CHECK EXISTS BECAUSE IT WAS NOT.
    # The HTML spec makes the FIRST `summary` child the disclosure control; mine sat after the body
    # div, so a browser supplies its own default summary and renders the "show all" label as
    # ordinary content INSIDE the thing it was meant to open.
    #
    # **None of the other checks could see it.** The DOM parser counts a `<summary>` tag wherever it
    # sits, so the tab-stop count read 50 either way, and every prohibition check was about tags
    # that are ABSENT. *A count of a tag is not a check of its position.*
    class FoldOrder(html.parser.HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.depth = 0
            self.first_child: list[str | None] = []

        def handle_starttag(self, tag, attrs):
            if tag == "details":
                self.depth += 1
                self.first_child.append(None)
            elif self.depth and self.first_child and self.first_child[-1] is None:
                self.first_child[-1] = tag

        def handle_endtag(self, tag):
            if tag == "details" and self.depth:
                self.depth -= 1

    order = FoldOrder()
    order.feed(first)
    check("every <details> has <summary> as its FIRST child element",
          bool(order.first_child) and all(c == "summary" for c in order.first_child),
          f"{len(order.first_child)} folds; first children seen: "
          f"{sorted(set(str(c) for c in order.first_child))}")

    print("\n" + "=" * 78)
    print(f"CHECKS {CHECKS}   PASS {CHECKS - len(FAILURES)}   FAIL {len(FAILURES)}")
    for name in FAILURES:
        print(f"  FAILED: {name}")
    print("STILL UNMET AND NOT CLOSED BY ANY OF THE ABOVE: acceptance clause 5 in a real browser,")
    print("clause 1 at 375px, clause 2 at a human keyboard, clause 6 at a served SHA.")
    print("=" * 78)
    shutil.rmtree(root, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
