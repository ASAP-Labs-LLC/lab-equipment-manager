"""Login → Instruments (it was a mode selector) → the floor or Checklists.

The floor used to be the whole app, which is wrong on a phone: an operator
walking the lab wants "checklists" or "the map", not a 3D floor plan they have
to pinch-zoom past. So the root is now a mode selector with two large targets,
and the floor moves to /floor.
"""
import re

import pytest

from labcore_gateway import FakeLabCoreGateway


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


@pytest.fixture
def client():
    from web_app import create_app
    app = create_app(FakeLabCoreGateway(), authenticator=StubAuth(),
                     secret="s")
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def home(client):
    return client.get("/").get_data(as_text=True)


class TestTheHomeIsInstruments:
    """The root was a chooser between two big buttons (Map, Checklists). It
    was a click tax with no way to QC, and the question nearly everyone
    brings — "can this instrument run?" — needed five clicks (ia-final §8,
    T1 baseline 5/0/6). Since 2026-10-01 the root IS Instruments: the answer
    is in the table on arrival, and the shell's nav carries Checklists, QC,
    Log and Settings one click away, as the chooser's two targets were."""

    def test_root_is_instruments_not_the_floor(self, client):
        body = client.get("/").get_data(as_text=True)
        assert "LAB FLOOR" not in body.upper()
        assert 'data-testid="instruments-page"' in body

    def test_the_two_old_ways_in_are_still_one_click(self, home):
        assert 'href="/floor"' in home          # the List · Floor map seg
        assert 'href="/checklists"' in home     # the nav

    def test_it_says_who_is_signed_in_here_too(self, home):
        assert 'data-testid="user-chip"' in home

    def test_it_carries_a_sign_in_route(self, home):
        assert "/signin" in home


class TestTheFloorMoved:
    def test_the_floor_is_at_slash_floor(self, client):
        body = client.get("/floor").get_data(as_text=True)
        assert "LAB FLOOR" in body.upper()

    def test_retired_pages_still_land_on_the_floor(self, client):
        for old in ("/stations", "/dashboard"):
            r = client.get(old)
            assert r.status_code in (301, 302), old
            landed = client.get(r.headers["Location"], follow_redirects=True)
            assert "LAB FLOOR" in landed.get_data(as_text=True).upper(), old

    def test_the_floor_can_get_back_to_the_selector(self, client):
        body = client.get("/floor").get_data(as_text=True)
        assert 'href="/"' in body


class TestChecklistsMode:
    def test_the_bookmark_lands_on_a_round(self, client):
        r = client.get("/checklists")
        assert r.status_code == 302
        assert client.get(r.headers["Location"]).status_code == 200

    def test_it_offers_opening_and_closing(self, client):
        """Ryan: Checklists → Open or close."""
        body = client.get("/checklists", follow_redirects=True).get_data(as_text=True)
        assert 'href="/checklists/opening"' in body and 'href="/checklists/closing"' in body

    def test_it_can_get_back_to_the_selector(self, client):
        assert 'href="/"' in client.get("/checklists", follow_redirects=True).get_data(as_text=True)

    def test_it_is_honest_that_nothing_is_configured_yet(self, client):
        """No round set up must say so, and say where to set one up."""
        client.get("/api/checklists")             # so the page knows, rather than reading
        body = client.get("/checklists/opening").get_data(as_text=True)
        assert "No opening round is set up." in body
        assert "/checklists/edit/new?slot=opening" in body


class TestEveryPageWorksOffline:
    """LabCore outages must not take the shell down (see test_offline_boot)."""

    @pytest.fixture
    def dead_client(self):
        from web_app import create_app

        class Dead:
            base_url = "https://labcore.example"

            def is_running(self):
                return False

            def sql(self, *a, **k):
                return {"error": "unreachable"}

            def write(self, *a, **k):
                return {"error": "unreachable"}

            def read_sql(self, *a, **k):
                return {"error": "unreachable"}

            def get_samples(self, **k):
                return None

            def get_test_names(self, **k):
                return None

        app = create_app(Dead(), authenticator=StubAuth(), secret="s")
        app.config["TESTING"] = True
        return app.test_client()

    @pytest.mark.parametrize("path", ["/", "/floor", "/checklists/opening", "/checklists/edit"])
    def test_it_still_renders(self, dead_client, path):
        assert dead_client.get(path).status_code == 200, path
