"""The front door is the Attention inbox, and the Queue keeps its own address.

A4 INFINITY-STREAMLINE, ALPHA-SPRINT-1, under D-ALPHA-UX-1 (2026-09-14), taking ALPHA-UX-INFINITY's
STREAMLINE item 1: a person opening `/` lands on the surface that answers "what needs me", which is
Attention, instead of the Queue's card wall. The Queue is one click away in the nav, at `/queue`,
unchanged. A route change and nothing else: no template, no verb and no store read on `/`.

Built through `create_app()` and Flask's test client, the way `test_terminal.py` builds it, so it
needs no console and no browser. A redirect needs no rows, but `create_app()` still asks the store
who it is at boot (`store.whoami`), and the store's default database is the LIVE `brain`. So this
file names a database that cannot be the live one BEFORE the app is imported, and refuses to run if
anything points it at `brain`: the boot read then fails soft, which is all a redirect needs.
"""

import os
import unittest

os.environ["BRAIN_PG_DB"] = os.environ.get("A4_FRONT_DOOR_DB", "brain_a4_front_door_no_such_db")
if os.environ["BRAIN_PG_DB"] == "brain":
    raise SystemExit("test_the_front_door: refusing the live store; this suite needs no rows")

from web.app import create_app  # noqa: E402  (the database name must be set first)


class TheFrontDoorIsAttention(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.rules = {str(rule.rule): rule for rule in cls.app.url_map.iter_rules()}
        print("\n  DENOMINATORS  routes %d" % len(cls.rules))

    def test_slash_redirects_to_the_attention_inbox(self):
        response = self.app.test_client().get("/")
        self.assertIn(response.status_code, (302, 303), response.status_code)
        self.assertTrue(response.headers["Location"].endswith("/attention/"), response.headers["Location"])

    def test_slash_is_not_the_queue_view_any_more(self):
        self.assertNotEqual(self.rules["/"].endpoint, "queue")

    def test_the_queue_keeps_its_own_address_and_view(self):
        self.assertIn("/queue", self.rules)
        self.assertEqual(self.rules["/queue"].endpoint, "queue")
        self.assertIn("/attention/", self.rules)

    def test_the_front_door_is_read_only(self):
        self.assertNotIn("POST", self.rules["/"].methods)

    # ------------------------------------------- the redirect carries its query (2026-09-16)

    def test_the_redirect_carries_theme_dark(self):
        """PINNED ON `dark`, AND THE DIRECTION IS THE WHOLE POINT.

        W3-P14-INSTRUMENT found that this redirect dropped everything after the `?`. MEASURED:
        `GET /?theme=light` answered `302 Location: /attention/`, parameter gone, so the
        destination fell to the pre-paint default.

        Before `e96bbd6` that default was DARK, so `/?theme=light` gave dark and was visibly
        wrong. After it the default is LIGHT, so `/?theme=light` now gives light FOR THE WRONG
        REASON and looks correct -- a fix that corrects the symptom a defect presents through can
        leave the defect and remove its only visible sign. The live symptom flipped to
        `/?theme=dark` giving light, which nobody asks for and nobody would notice.

        So this case asks for `dark`: the one direction that can still fail. A case on
        `?theme=light` would pass today over a redirect that discards everything.
        """
        loc = self.app.test_client().get("/?theme=dark").headers["Location"]
        self.assertIn("theme=dark", loc, loc)

    def test_the_redirect_carries_any_query_not_just_theme(self):
        """The defect is not about theme, so neither is the fix.

        A 302 that discards its query discards EVERY parameter, present and future. `theme` is
        only the one visible today because something downstream reads it; a deep link, a filter or
        an invite token would vanish the same way and be debugged from nothing. Asserting only the
        theme case would let the next parameter be dropped silently.
        """
        loc = self.app.test_client().get("/?first=1&second=two").headers["Location"]
        self.assertIn("first=1", loc, loc)
        self.assertIn("second=two", loc, loc)

    def test_control_a_bare_slash_gains_no_empty_question_mark(self):
        """The nonzero beside the zero. Appending unconditionally would make every plain visit
        land on `/attention/?`, which is a different URL, a cache miss and an ugly address bar."""
        loc = self.app.test_client().get("/").headers["Location"]
        self.assertNotIn("?", loc, loc)
        self.assertTrue(loc.endswith("/attention/"), loc)

    def test_control_a_query_cannot_move_the_destination(self):
        """The query is appended to a fixed internal path from `url_for`, so nothing a caller
        sends can change where the redirect goes. Pinned rather than reasoned about."""
        for hostile in ("?x=1", "?next=https://example.com", "?a=../../elsewhere"):
            loc = self.app.test_client().get("/" + hostile).headers["Location"]
            path = loc.split("?", 1)[0]
            self.assertTrue(path.endswith("/attention/"), (hostile, loc))


if __name__ == "__main__":
    unittest.main()
