"""Login → mode selector → Map or Checklists.

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


class TestTheModeSelector:
    def test_root_is_the_mode_selector_not_the_floor(self, client):
        body = client.get("/").get_data(as_text=True)
        assert "LAB FLOOR" not in body.upper()

    def test_it_offers_exactly_two_ways_in(self, home):
        assert 'href="/floor"' in home
        assert 'href="/checklists"' in home

    def test_both_targets_are_named_plainly(self, home):
        assert re.search(r"Map|Floor", home)
        assert "Checklist" in home

    def test_the_targets_are_big_enough_for_a_gloved_thumb(self, home):
        """Two big buttons was the requirement, so the choice must not be a
        pair of text links."""
        assert 'class="mode"' in home

    def test_it_says_who_is_signed_in_here_too(self, home):
        assert 'id="who"' in home

    def test_it_carries_a_sign_in_route(self, home):
        assert "/api/login" in home or 'id="btnAuth"' in home


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
    def test_the_page_exists(self, client):
        assert client.get("/checklists").status_code == 200

    def test_it_offers_opening_and_closing(self, client):
        """Ryan: Checklists → Open or close."""
        body = client.get("/checklists").get_data(as_text=True).lower()
        assert "open" in body and "clos" in body

    def test_it_can_get_back_to_the_selector(self, client):
        assert 'href="/"' in client.get("/checklists").get_data(as_text=True)

    def test_it_is_honest_that_nothing_is_configured_yet(self, client):
        """Checklists aren't built yet — the page must not imply they are."""
        body = client.get("/checklists").get_data(as_text=True).lower()
        assert "not" in body and "yet" in body


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

    @pytest.mark.parametrize("path", ["/", "/floor", "/checklists"])
    def test_it_still_renders(self, dead_client, path):
        assert dead_client.get(path).status_code == 200, path
