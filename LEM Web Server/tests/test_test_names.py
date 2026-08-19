"""The method list must actually arrive.

`/api/test-names` was returning an empty list against a healthy LabCore holding
282 methods, so the assay picker was blank and no QC could be wired at all.
Two independent faults stacked:

  1. LabCore answers `{"tests": [...]}`; the vendored client reads
     `data.get("test_names", [])` and so always saw nothing. That bug is in
     LabLink's own client (LabStation and LabEntry have it, LabOut does not) —
     we are not editing the vendored file, we override it in our gateway, which
     is the same reason HttpLabCoreGateway already overrides base_url.
  2. The fallback `SELECT DISTINCT test_name FROM sample_tests` scans 342k rows
     and blew the client's 8s read timeout, so the safety net was empty too.

Reading the right key takes 0.3s for all 282 names.
"""
import pytest

from labcore_gateway import FakeLabCoreGateway, HttpLabCoreGateway


class StubClient:
    """Stands in for the vendored client: records how it was called."""

    def __init__(self, payload, expect_error=False):
        self.payload = payload
        self.calls = []
        self.expect_error = expect_error

    def get_test_names_raw(self, timeout=None):
        self.calls.append(timeout)
        if self.expect_error:
            raise RuntimeError("LabCore unreachable")
        return self.payload


@pytest.fixture
def gw():
    """An HttpLabCoreGateway with the network swapped out."""
    g = HttpLabCoreGateway.__new__(HttpLabCoreGateway)
    g.base_url = "https://labcore.test"
    g._client = None
    return g


def with_payload(gw, payload, **kw):
    gw._client = StubClient(payload, **kw)
    return gw


# ── the key LabCore actually uses ───────────────────────────────────────────

class TestReadsTheRightKey:
    def test_it_reads_the_tests_key(self, gw):
        """This is what a live LabCore returns."""
        with_payload(gw, {"tests": ["Sulfur", "Water, by Karl Fischer"]})
        assert gw.get_test_names() == ["Sulfur", "Water, by Karl Fischer"]

    def test_a_flat_list_still_works(self, gw):
        """Older LabCore versions answered with a bare list."""
        with_payload(gw, ["Sulfur", "Flash Point"])
        assert gw.get_test_names() == ["Sulfur", "Flash Point"]

    def test_the_legacy_test_names_key_still_works(self, gw):
        """Defensive: don't trade one hard-coded key for another."""
        with_payload(gw, {"test_names": ["Sulfur"]})
        assert gw.get_test_names() == ["Sulfur"]

    def test_an_empty_catalogue_is_an_empty_list(self, gw):
        with_payload(gw, {"tests": []})
        assert gw.get_test_names() == []

    def test_an_unreachable_labcore_is_none_not_empty(self, gw):
        """None means "couldn't ask"; [] means "asked, there are none". The
        caller falls back only on the former."""
        with_payload(gw, None, expect_error=True)
        assert gw.get_test_names() is None

    def test_a_junk_payload_is_none_rather_than_a_crash(self, gw):
        with_payload(gw, "not a payload at all")
        assert gw.get_test_names() is None

    def test_blank_names_are_dropped(self, gw):
        with_payload(gw, {"tests": ["Sulfur", "", None, "  ", "Flash Point"]})
        assert gw.get_test_names() == ["Sulfur", "Flash Point"]


class TestItAsksPatiently:
    def test_it_passes_a_generous_timeout(self, gw):
        """The default 8s loses to a DISTINCT scan on a busy LabCore — the
        vendored client's own docstring says to pass something generous."""
        client = with_payload(gw, {"tests": ["Sulfur"]})._client
        gw.get_test_names()
        assert client.calls, "the client was never called"
        assert client.calls[0] is not None and client.calls[0] >= 30


# ── the endpoint the picker calls ───────────────────────────────────────────

class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


class CountingFake(FakeLabCoreGateway):
    """A fake that reports a catalogue and counts how often it is asked."""

    def __init__(self, names=("Sulfur", "ASTM D6304 - Water, by Karl Fischer")):
        super().__init__()
        self.names = list(names)
        self.name_calls = 0

    def get_test_names(self, **kw):
        self.name_calls += 1
        return list(self.names)


class TestEndpoint:
    def client_for(self, gateway):
        from web_app import create_app
        app = create_app(gateway, authenticator=StubAuth(), secret="s")
        app.config["TESTING"] = True
        return app.test_client()

    def test_the_names_reach_the_picker(self):
        gw = CountingFake()
        body = self.client_for(gw).get("/api/test-names").get_json()
        assert body["tests"] == ["Sulfur",
                                 "ASTM D6304 - Water, by Karl Fischer"]

    def test_it_is_not_re_fetched_on_every_open(self):
        """The picker asks each time it opens; the catalogue barely changes."""
        gw = CountingFake()
        client = self.client_for(gw)
        for _ in range(4):
            client.get("/api/test-names")
        assert gw.name_calls == 1

    def test_an_empty_answer_is_not_cached(self):
        """Caching a blank list would keep the picker empty until restart —
        exactly the failure this is meant to end."""
        gw = CountingFake(names=[])
        client = self.client_for(gw)
        client.get("/api/test-names")
        client.get("/api/test-names")
        assert gw.name_calls == 2

    def test_it_falls_back_when_the_endpoint_cannot_answer(self):
        """None from the gateway = couldn't ask; the DISTINCT scan is the net."""
        class Mute(FakeLabCoreGateway):
            def get_test_names(self, **kw):
                return None

        gw = Mute()
        gw.sql("CREATE TABLE IF NOT EXISTS sample_tests (lab_id TEXT, "
               "test_name TEXT, result TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO sample_tests VALUES (?,?,?,?)",
               ["1", "Flash Point", "62", "2026-08-03"])
        body = self.client_for(gw).get("/api/test-names").get_json()
        assert body["tests"] == ["Flash Point"]
