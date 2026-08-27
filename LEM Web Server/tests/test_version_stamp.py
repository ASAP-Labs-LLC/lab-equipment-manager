"""The running version, visible on the page.

Ryan: *"add the version number of the program to the bottom right in the
website, so I know which one is live going forward."*

The deploy is unattended — `C:\\ASAPApps\\updater` stages a release, health-checks
it and swaps the junction on its own once nobody has written for five minutes.
So the version on the floor is not something anybody chose to install at a
moment they would remember, and "which one is live?" has until now meant reading
`/healthz` or the updater log. A wall display should be able to answer it.

Two things worth pinning rather than assuming:

**It is the SAME string `/healthz` reports.** Two version stamps that can differ
are worse than one, because the one on the wall is the one people will quote.

**A checkout says `dev`, and that is correct.** CI writes `VERSION` into the
release archive and the file is gitignored, so a developer's browser saying
`dev` is the honest answer rather than a bug — but it must not say nothing at
all, because a blank corner reads as "no version" rather than "not a release".
"""

import re

import pytest

import web_app
from labcore_gateway import FakeLabCoreGateway
from web_app import create_app

# Every page that includes the shared nav. The stamp lives there so it cannot
# be on some pages and not others.
PAGES = ("/", "/floor", "/maintenance", "/checklists", "/logs")


@pytest.fixture
def client():
    app = create_app(FakeLabCoreGateway(), secret="t")
    app.config.update(TESTING=True)
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = "ryan"
    return c


class TestTheVersionIsOnThePage:
    @staticmethod
    def _stamp(body):
        """The stamp's own text — NOT the whole page.

        A first draft asserted `APP_VERSION in body`, which passed before the
        feature existed: a checkout's version is the string "dev", and "dev"
        occurs in the markup by coincidence. Eight of eleven tests here were
        green against a page with no stamp on it at all. Read the element.
        """
        m = re.search(r'<(\w+)[^>]*class="[^"]*verstamp[^"]*"[^>]*>(.*?)</\1>',
                      body, re.S)
        return re.sub(r"<[^>]+>", " ", m.group(2)) if m else None

    @pytest.mark.parametrize("page", PAGES)
    def test_every_page_carries_it(self, client, page):
        text = self._stamp(client.get(page).get_data(as_text=True))
        assert text is not None, "%s has no stamp element" % page
        assert web_app.APP_VERSION in text, (page, text.strip()[:80])

    def test_it_is_the_same_string_healthz_reports(self, client):
        health = client.get("/healthz").get_json()["version"]
        text = self._stamp(client.get("/floor").get_data(as_text=True))
        assert text and health in text
        assert health == web_app.APP_VERSION

    def test_a_checkout_says_dev_rather_than_nothing(self, client):
        """A blank corner reads as "no version", not as "not a release"."""
        assert web_app.APP_VERSION
        body = client.get("/floor").get_data(as_text=True)
        assert re.search(r'class="[^"]*verstamp', body), (
            "the stamp element is not on the page")

    def test_it_names_itself_for_someone_reading_it_aloud(self, client):
        """"3.1.0" alone in a corner is a number nobody can act on. It has to
        say what it is the version OF."""
        body = client.get("/floor").get_data(as_text=True)
        text = self._stamp(body)
        assert text, "no stamp element"
        assert "LEM" in text or "version" in text.lower(), text.strip()[:80]


class TestItReflectsWhatIsActuallyRunning:
    def test_a_release_stamp_is_shown_verbatim(self, tmp_path, monkeypatch):
        """The updater swaps a junction under a running service; the stamp has
        to be whatever the RELEASE says, not something the page composed."""
        (tmp_path / "VERSION").write_text("v9.9.9\n", encoding="utf-8")
        assert web_app.read_version(tmp_path) == "v9.9.9"

    def test_an_unreadable_version_file_is_dev_not_a_crash(self, tmp_path):
        """`read_version` never raises: /healthz reporting "dev" is a nuisance,
        /healthz returning 500 makes a working release look broken and triggers
        a rollback that was never needed. The page inherits that."""
        assert web_app.read_version(tmp_path / "nope") == "dev"

    def test_the_stamp_is_not_a_link_or_a_control(self, client):
        """It reports; it does not do anything. A clickable version in the
        corner of a wall display is a thing somebody leans on by accident."""
        body = client.get("/floor").get_data(as_text=True)
        stamp = re.search(r'<(\w+)[^>]*verstamp', body)
        assert stamp and stamp.group(1).lower() not in ("a", "button")
