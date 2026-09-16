"""No "Served commit" footer under every room; the commit is still readable where a build is checked.

A4 INFINITY-STREAMLINE, ALPHA-SPRINT-1, Andrew's D-INFINITY-UX-RULINGS-1 (2026-09-14, relayed by
CLOSEDOWN-COMMANDER): remove the served-commit footer, resolve the contradiction in
web/views/tests/test_visual_vocabulary_and_served_commit.py in favour of no footer, and keep the
served commit asserted at GET /api/health.

The contradiction it resolves: `TheShellExposesNoCommitToAnyTemplate` wants no element and no
`served_commit` in app.py, while the footer plus the global put one on every page and made the
proposal class count two. With both gone, all four of those cases pass unedited.

Stub read ports on a real Flask app for the page; app.py source for the health report.
"""

import os
import re
import unittest

from web.tests.test_routines_and_attention_render import an_app

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PY = os.path.join(WEB_DIR, "app.py")
BASE_HTML = os.path.join(WEB_DIR, "templates", "base.html")


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def without_jinja_comments(text):
    return re.sub(r"\{#.*?#\}", "", text, flags=re.S)


class NoFooterOnThePage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.body = an_app().test_client().get("/attention/").get_data(as_text=True)

    def test_the_page_rendered(self):
        self.assertIn("<h1>Attention</h1>", self.body)

    def test_no_served_commit_element_or_words_on_the_page(self):
        self.assertNotIn("data-served-commit", self.body)
        self.assertNotIn("Served commit", self.body)
        self.assertNotIn("served-build", self.body)

    def test_the_shell_template_carries_neither_the_footer_nor_its_style(self):
        live = without_jinja_comments(read(BASE_HTML))
        for gone in ("data-served-commit", "Served commit", "served-build", "served_commit"):
            self.assertNotIn(gone, live, gone)


class TheCommitIsStillReported(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = read(APP_PY)

    def test_no_template_global_carries_it(self):
        self.assertNotIn('globals["served_commit"]', self.app)

    def test_health_still_reports_the_serving_commit(self):
        self.assertIn('out["serving"] = _serving_report(serving_commit, serving_started_at, serving_mtimes)',
                      self.app)
        self.assertIn('"commit_now": commit_now', self.app)

    def test_every_response_still_names_it_in_a_header(self):
        self.assertIn('response.headers["X-Console-Commit"] = serving_commit or "unknown"', self.app)


if __name__ == "__main__":
    unittest.main()
