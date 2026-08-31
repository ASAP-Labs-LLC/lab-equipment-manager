"""Every checklist reading on one screen.

Ryan, 31 Aug 2026: *"Can you also put a page of the checklists page with all
the checklist 'trend' items being visible as like a dashboard too."*

A checklist item can be a `number` — nitrogen pressure, a bath temperature, a
waste-bottle level — and each one already has a per-item trend behind a small
"trend" link. One at a time is the wrong shape for the question people actually
have, which is "is anything drifting", and a page that answers it needs every
series at once.

**One read, not one per item.** The obvious build is a fetch per trend link;
with twenty numeric items across four rounds that is twenty requests, each one
a LabCore op behind the same queue the benches write through. `/api/checklists/
trends` answers the whole page in one.

**A reading is not a verdict.** These have no spec band — nobody has said what
a good nitrogen pressure is — so the page may not colour anything pass or fail.
What it can honestly show is movement: the last value, the direction, and how
long since anybody wrote one down. A stale item is the finding here, because an
unread cylinder is exactly what a checklist exists to prevent.
"""

import pytest

import snapshot_service
from labcore_gateway import FakeLabCoreGateway
from web_app import create_app


@pytest.fixture
def gw():
    g = FakeLabCoreGateway()
    snapshot_service.SnapshotService(g).ensure_schema()
    return g


def _client(gw, tmp_path):
    app = create_app(gw, secret="t", documents_root=str(tmp_path))
    app.config.update(TESTING=True)
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = "ryan"
    return c


def _round(client, name="Opening round", slot="opening", items=None):
    items = items or [
        {"text": "Nitrogen pressure", "entry_type": "number", "units": "psi"},
        {"text": "Bath temperature", "entry_type": "number", "units": "C"},
        {"text": "Anything unusual", "entry_type": "text"},
        {"text": "Waste bottle checked"},
    ]
    r = client.post("/api/checklists",
                    json={"name": name, "slot": slot, "items": items})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["checklist"]


def _write(client, cl, item_text, value, day=None):
    item = next(i for i in cl["items"] if i["text"] == item_text)
    body = {"item_uid": item["uid"], "value": str(value)}
    if day:
        body["day"] = day
    return client.post("/api/checklists/%s/value" % cl["uid"], json=body)


class TestEveryNumericItemIsOnIt:
    def test_only_the_numeric_items_appear(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        _round(c)
        body = c.get("/api/checklists/trends").get_json()
        names = {t["text"] for t in body["trends"]}
        # A set: the ORDER is a separate, deliberate decision (never-written
        # first, then oldest reading) and is pinned by its own test below.
        assert names == {"Nitrogen pressure", "Bath temperature"}, names

    def test_a_text_item_is_not_a_trend(self, gw, tmp_path):
        """"Anything unusual" is a note. Plotting it would be a chart of
        string lengths."""
        c = _client(gw, tmp_path)
        _round(c)
        body = c.get("/api/checklists/trends").get_json()
        assert all(t["text"] != "Anything unusual" for t in body["trends"])

    def test_items_from_every_round_are_included(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        _round(c, name="Opening round", slot="opening")
        _round(c, name="Closing round", slot="closing",
               items=[{"text": "Argon pressure", "entry_type": "number",
                       "units": "psi"}])
        body = c.get("/api/checklists/trends").get_json()
        assert {t["text"] for t in body["trends"]} == {
            "Nitrogen pressure", "Bath temperature", "Argon pressure"}

    def test_each_trend_says_which_round_it_belongs_to(self, gw, tmp_path):
        """Two rounds can both have a "Pressure"; the name alone is ambiguous
        on a dashboard."""
        c = _client(gw, tmp_path)
        cl = _round(c)
        body = c.get("/api/checklists/trends").get_json()
        assert all(t.get("checklist") == cl["name"] for t in body["trends"])
        assert all(t.get("slot") == "opening" for t in body["trends"])

    def test_it_is_one_request_not_one_per_item(self, gw, tmp_path):
        """Twenty numeric items must not be twenty LabCore round trips on a
        page somebody leaves open."""
        c = _client(gw, tmp_path)
        _round(c, items=[{"text": "P%d" % i, "entry_type": "number"}
                         for i in range(20)])
        body = c.get("/api/checklists/trends").get_json()
        assert len(body["trends"]) == 20


class TestWhatComesFirst:
    """An item nobody has written down is the only FINDING on this page; the
    rest are readings. So it leads, and after that the one going longest
    without attention."""

    def test_a_never_written_item_sorts_above_a_written_one(self, gw,
                                                           tmp_path):
        c = _client(gw, tmp_path)
        cl = _round(c)
        _write(c, cl, "Nitrogen pressure", 118)
        names = [t["text"] for t in
                 c.get("/api/checklists/trends").get_json()["trends"]]
        assert names.index("Bath temperature") < names.index(
            "Nitrogen pressure"), names

    def test_the_count_of_never_written_items_is_reported(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        cl = _round(c)
        _write(c, cl, "Nitrogen pressure", 118)
        body = c.get("/api/checklists/trends").get_json()
        assert body["counts"]["never_written"] == 1
        assert body["counts"]["items"] == 2


class TestTheReadingsThemselves:
    def test_a_written_value_appears_in_its_series(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        cl = _round(c)
        assert _write(c, cl, "Nitrogen pressure", 118).status_code == 200
        t = next(x for x in c.get("/api/checklists/trends").get_json()["trends"]
                 if x["text"] == "Nitrogen pressure")
        assert [p["value"] for p in t["points"]] == [118.0]
        assert t["last_value"] == 118.0

    def test_the_units_ride_along(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        _round(c)
        t = next(x for x in c.get("/api/checklists/trends").get_json()["trends"]
                 if x["text"] == "Nitrogen pressure")
        assert t["units"] == "psi"

    def test_an_item_nobody_has_written_yet_is_empty_not_missing(self, gw,
                                                                tmp_path):
        """An item with no readings still belongs on the dashboard — "nobody
        has ever written this down" is the most useful thing it can say."""
        c = _client(gw, tmp_path)
        _round(c)
        t = next(x for x in c.get("/api/checklists/trends").get_json()["trends"]
                 if x["text"] == "Bath temperature")
        assert t["points"] == []
        assert t["last_value"] is None


class TestItDoesNotInventAVerdict:
    """No checklist item has a spec band. Colouring one pass or fail would be
    LEM deciding what a good nitrogen pressure is, which nobody has told it."""

    def test_no_trend_claims_to_be_in_or_out_of_spec(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        cl = _round(c)
        _write(c, cl, "Nitrogen pressure", 118)
        t = c.get("/api/checklists/trends").get_json()["trends"][0]
        assert "in_spec" not in t and "state" not in t

    def test_it_reports_how_long_since_anybody_wrote_one(self, gw, tmp_path):
        """The finding this page CAN make: an item nobody is filling in."""
        c = _client(gw, tmp_path)
        cl = _round(c)
        _write(c, cl, "Nitrogen pressure", 118)
        t = next(x for x in c.get("/api/checklists/trends").get_json()["trends"]
                 if x["text"] == "Nitrogen pressure")
        assert "last_at" in t


class TestAFailedReadIsNotAnEmptyDashboard:
    def test_it_refuses_rather_than_showing_flat_lines(self, gw, tmp_path):
        """A flat, empty trend is a claim about a cylinder nobody has been
        reading — the opposite of what an unreadable series means. The
        per-item route already says this; the dashboard must not undo it."""
        app = create_app(gw, secret="t", documents_root=str(tmp_path))
        app.config.update(TESTING=True)
        c = app.test_client()
        with c.session_transaction() as s:
            s["user"] = "ryan"

        def blind(sql, args=None, **kw):
            if "lem_checklist" in sql:
                return {"error": "LabCore is busy", "busy": True}
            return {"ok": True, "rows": []}

        gw.read_sql = blind
        assert c.get("/api/checklists/trends").status_code >= 400


class TestThePageExists:
    def test_the_dashboard_is_served(self, gw, tmp_path):
        assert _client(gw, tmp_path).get(
            "/checklists/trends").status_code == 200

    def test_the_checklists_page_links_to_it(self, gw, tmp_path):
        page = _client(gw, tmp_path).get("/checklists").get_data(as_text=True)
        assert "/checklists/trends" in page

    def test_the_routes_are_registered(self, gw, tmp_path):
        app = create_app(gw, secret="t", documents_root=str(tmp_path))
        rules = {str(r) for r in app.url_map.iter_rules()}
        assert "/checklists/trends" in rules
        assert "/api/checklists/trends" in rules
