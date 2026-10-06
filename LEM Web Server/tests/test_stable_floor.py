"""The floor must not rearrange itself while you are looking at it.

Ryan, 2026-08-03: "everytime this thing refreshes it changes layout and a ton of
extra stuff, please just make it stable".

Two causes, both real:

1. `build_machines` sorted by `updated_at` **descending**. Instruments report every
   ~40 seconds, so the order of the payload churned constantly — and anything
   derived from array order churned with it.

2. Two instruments are saved on the SAME bay (OptiMPP 2 and PAC Flash 2, both at
   4.1,0.0). The painter sort keys on `gx+gy`, which ties, and `Array.sort` is
   stable — so the tie was broken by payload order, i.e. by whichever reported last.
   One machine flipped on top of the other every refresh, and the one underneath was
   invisible.

The order is now derived from the instrument, not from when it last spoke.
"""
from datetime import datetime

import pytest

from labcore_gateway import FakeLabCoreGateway


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


MACHINES = [
    ("b2ce21612b3c", "OptiMPP 1", "2026-08-03T21:41:20"),
    ("2a49a1320ca1", "OptiMPP 2", "2026-08-03T21:47:24"),
    ("5fd04c0031f9", "PAC Flash 1", "2026-08-03T21:43:40"),
    ("7e8304c31983", "PAC Flash 2", "2026-08-03T21:46:28"),
    ("844337a2ba08", "Multitek NS", "2026-08-03T21:46:45"),
    ("300f71750e3e", "Multitek S", "2026-08-03T21:47:23"),
]


@pytest.fixture
def gw():
    g = FakeLabCoreGateway()
    g.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
          "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
          "reason TEXT, updated_at TEXT)")
    for uid, title, ts in MACHINES:
        g.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
              [uid, title, "GREEN", "ok", ts])
    return g


@pytest.fixture
def client(gw):
    from web_app import create_app
    app = create_app(gw, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


def order(client):
    body = client.get("/api/machines?fresh=1").get_json()
    return [m["title"] for m in body["machines"]]


class TestTheOrderIsStable:
    def test_it_does_not_depend_on_who_reported_last(self, gw, client):
        first = order(client)
        gw.sql("UPDATE lem_machine_status SET updated_at = ? "
               "WHERE title = 'OptiMPP 1'", ["2026-08-03T23:59:59"])
        assert order(client) == first, "reporting moved an instrument in the list"

    def test_every_refresh_gives_the_same_order(self, gw, client):
        seen = {tuple(order(client)) for _ in range(5)}
        assert len(seen) == 1

    def test_it_is_ordered_by_the_instrument_itself(self, gw, client):
        assert order(client) == sorted(t for _u, t, _ts in MACHINES)

    def test_a_new_instrument_lands_in_its_place_not_at_the_top(self, gw, client):
        order(client)
        gw.sql("INSERT INTO lem_machine_status VALUES "
               "('zz','Nova Titrator','GREEN','ok','2026-08-03T23:59:59')")
        got = order(client)
        assert got.index("Nova Titrator") == sorted(got).index("Nova Titrator")

    def test_two_instruments_sharing_a_title_still_order_deterministically(self, gw,
                                                                          client):
        """Titles are not unique — a duplicated config can share one. The uid is
        the tiebreak, so the order stays fixed rather than following recency."""
        gw.sql("INSERT INTO lem_machine_status VALUES "
               "('aaa','OptiMPP 1','GREEN','ok','2026-08-03T22:00:00')")
        first = [(m["title"], m["machine_uid"]) for m in
                 client.get("/api/machines?fresh=1").get_json()["machines"]]
        gw.sql("UPDATE lem_machine_status SET updated_at='2026-08-04T01:00:00' "
               "WHERE machine_uid='aaa'")
        second = [(m["title"], m["machine_uid"]) for m in
                  client.get("/api/machines?fresh=1").get_json()["machines"]]
        assert first == second


class TestRecencyIsStillAvailable:
    def test_each_machine_still_carries_its_own_timestamp(self, gw, client):
        """Stable ordering must not cost the information — the feed and the "ago"
        stamps need it, they just should not drive layout."""
        body = client.get("/api/machines?fresh=1").get_json()
        for m in body["machines"]:
            assert m["updated_at"]
            assert "last_activity" in m

    def test_the_most_recent_is_still_findable(self, gw, client):
        body = client.get("/api/machines?fresh=1").get_json()
        newest = max(body["machines"], key=lambda m: m["updated_at"])
        assert newest["title"] == "OptiMPP 2"


class TestTheFloorRefreshesFromTheServerNotItsOwnCache:
    """Ryan: "I thought the one server thing was supposed to hold the information
    until it itself refreshes, and then update the rest."

    That is exactly right, and it was being undone on the client. The floor called
    `LEM.get()` on its 30s timer; `get()` resolves on the FIRST paint, which is the
    cached one — so the fresh answer landed in sessionStorage and only reached the
    screen on the NEXT tick. The floor ran permanently one cycle behind, and each
    tick repainted with an order that had since moved on.

    The server-side snapshot IS the cache now, and it answers in under a
    millisecond. The client cache is only there so arriving from another page is
    instant.
    """

    # The two tests that read the old floor page's load() went with it (piece 14).
    # The new pages paint first from the answer the server renders into the
    # page and then refetch from the network on the live feed, so there is
    # no client cache between them and the server to run a cycle behind.
    # lem.js keeps fresh() for the round pages that still load it.

    def test_lem_js_exposes_fresh(self):
        import pathlib
        js = (pathlib.Path(__file__).resolve().parent.parent
              / "static" / "lem.js").read_text(encoding="utf-8")
        assert "function fresh(" in js
        assert "fresh: fresh" in js

    def test_live_can_be_told_what_counts_as_a_change(self):
        """/api/machines carries age_seconds, which moves every request — a full
        JSON compare was never equal, so the repaint guard did nothing."""
        import pathlib
        js = (pathlib.Path(__file__).resolve().parent.parent
              / "static" / "lem.js").read_text(encoding="utf-8")
        assert "opts.signature" in js
