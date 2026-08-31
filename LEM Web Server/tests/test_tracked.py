"""A tracked thing, measured by more than one round.

Ryan, 31 Aug 2026: *"Opening and closing need to intersect. So we have to make
the things that are trending into an object (with a minimum and a maximum
volume) and then you can put in 'track' and select the object you want to
track… And convert the exist checklists with matching names into those objects
with history."*

Today a `number` item owns its readings. So "Nitrogen pressure" on the opening
round and "Nitrogen pressure" on the closing round are two unrelated series
that happen to share a name — which is the wrong shape for the thing being
measured. The cylinder does not care which round looked at it; it has one
pressure, read twice a day, and the interesting fact (it is going down) is
invisible while the two halves are kept apart.

So the THING becomes the object. A tracked item has a name, units, and a
minimum and maximum. A checklist item points at one, and every reading written
against any item pointing at it lands in the same series.

**No reading moves.** Readings live in `lem_checklist_state` keyed by
`(checklist_uid, item_uid)`, and the merge happens when they are read, by
grouping on what the item TRACKS rather than on the item. So the conversion is
reversible — clearing `track_uid` puts everything back exactly as it was — and
no history is rewritten, which is the only safe way to touch a compliance
record.

**A minimum and a maximum change what the dashboard may say.** Until now no
checklist item had a band, so the trends page was forbidden to colour anything
pass or fail — it would have been LEM inventing a limit. An operator-supplied
min and max is not invented, so a tracked object CAN be out of range, and
saying so is the point of having limits at all.
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


def _round(c, name, slot, items):
    r = c.post("/api/checklists", json={"name": name, "slot": slot,
                                        "items": items})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["checklist"]


def _num(text, units="psi"):
    return {"text": text, "entry_type": "number", "units": units}


def _write(c, cl, text, value, day):
    item = next(i for i in cl["items"] if i["text"] == text)
    return c.post("/api/checklists/%s/value" % cl["uid"],
                  json={"item_uid": item["uid"], "value": str(value),
                        "day": day})


def _both_rounds(c):
    """The shape Ryan described: the same cylinder on two rounds."""
    a = _round(c, "Opening round", "opening",
               [_num("Nitrogen pressure"), _num("Bath temperature", "C")])
    b = _round(c, "Closing round", "closing",
               [_num("Nitrogen pressure"), _num("Helium pressure")])
    return a, b


class TestATrackedThingExists:
    def test_one_can_be_made(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        r = c.post("/api/tracked", json={"name": "Nitrogen cylinder",
                                         "units": "psi", "min": 500,
                                         "max": 2200})
        assert r.status_code == 200, r.get_json()
        t = r.get_json()["tracked"]
        assert t["name"] == "Nitrogen cylinder" and t["uid"]
        assert t["min"] == 500 and t["max"] == 2200

    def test_it_comes_back_in_the_list(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        c.post("/api/tracked", json={"name": "Nitrogen cylinder", "min": 500,
                                     "max": 2200})
        names = [t["name"] for t in c.get("/api/tracked").get_json()["tracked"]]
        assert names == ["Nitrogen cylinder"]

    def test_a_nameless_one_is_refused(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        assert c.post("/api/tracked", json={"min": 1, "max": 2}
                      ).status_code == 400

    def test_a_maximum_below_the_minimum_is_refused(self, gw, tmp_path):
        """Reversed limits make every reading simultaneously too high and too
        low, and the page would report every cylinder as failing."""
        c = _client(gw, tmp_path)
        r = c.post("/api/tracked", json={"name": "Backwards", "min": 100,
                                         "max": 10})
        assert r.status_code == 400
        assert "min" in str(r.get_json()).lower()

    def test_limits_are_optional(self, gw, tmp_path):
        """A thing worth tracking before anybody has decided its limits is
        still worth tracking. It simply cannot be out of range yet."""
        c = _client(gw, tmp_path)
        r = c.post("/api/tracked", json={"name": "Waste bottle"})
        assert r.status_code == 200
        assert r.get_json()["tracked"]["min"] is None

    def test_the_limits_can_be_set_later(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        uid = c.post("/api/tracked", json={"name": "Waste bottle"}
                     ).get_json()["tracked"]["uid"]
        r = c.post("/api/tracked/%s" % uid, json={"min": 0, "max": 80})
        assert r.status_code == 200
        assert r.get_json()["tracked"]["max"] == 80

    def test_an_anonymous_write_is_refused(self, gw, tmp_path):
        app = create_app(gw, secret="t", documents_root=str(tmp_path))
        app.config.update(TESTING=True)
        assert app.test_client().post(
            "/api/tracked", json={"name": "X"}).status_code == 401


class TestTwoRoundsBecomeOneSeries:
    """The whole point: opening and closing intersect."""

    def _tracked_pair(self, c):
        a, b = _both_rounds(c)
        uid = c.post("/api/tracked", json={"name": "Nitrogen cylinder",
                                           "units": "psi", "min": 500,
                                           "max": 2200}
                     ).get_json()["tracked"]["uid"]
        for cl in (a, b):
            item = next(i for i in cl["items"]
                        if i["text"] == "Nitrogen pressure")
            r = c.post("/api/checklists/%s/track" % cl["uid"],
                       json={"item_uid": item["uid"], "tracked_uid": uid})
            assert r.status_code == 200, r.get_json()
        return a, b, uid

    def test_readings_from_both_rounds_land_in_one_series(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        a, b, uid = self._tracked_pair(c)
        _write(c, a, "Nitrogen pressure", 2100, "2026-08-30")
        _write(c, b, "Nitrogen pressure", 2050, "2026-08-30")
        _write(c, a, "Nitrogen pressure", 2000, "2026-08-31")
        t = next(x for x in c.get("/api/checklists/trends").get_json()["trends"]
                 if x.get("tracked_uid") == uid)
        assert [p["value"] for p in t["points"]] == [2100.0, 2050.0, 2000.0]

    def test_the_series_is_named_for_the_thing_not_the_round(self, gw,
                                                             tmp_path):
        c = _client(gw, tmp_path)
        _, _, uid = self._tracked_pair(c)
        t = next(x for x in c.get("/api/checklists/trends").get_json()["trends"]
                 if x.get("tracked_uid") == uid)
        assert t["text"] == "Nitrogen cylinder"

    def test_it_says_which_rounds_feed_it(self, gw, tmp_path):
        """An operator has to know where a reading comes from."""
        c = _client(gw, tmp_path)
        _, _, uid = self._tracked_pair(c)
        t = next(x for x in c.get("/api/checklists/trends").get_json()["trends"]
                 if x.get("tracked_uid") == uid)
        assert set(t["rounds"]) == {"Opening round", "Closing round"}

    def test_an_untracked_item_is_still_its_own_series(self, gw, tmp_path):
        """Nothing is forced into an object. An item nobody has pointed at a
        tracked thing keeps working exactly as before."""
        c = _client(gw, tmp_path)
        self._tracked_pair(c)
        names = [t["text"] for t in
                 c.get("/api/checklists/trends").get_json()["trends"]]
        assert "Bath temperature" in names and "Helium pressure" in names


class TestNowThereAreLimitsItCanSaySoTF:
    """With an operator-supplied min and max, "out of range" stops being an
    invention and becomes the reason the limits were entered."""

    def _one(self, c, lo=500, hi=2200):
        cl = _round(c, "Opening round", "opening", [_num("Nitrogen pressure")])
        uid = c.post("/api/tracked", json={"name": "Nitrogen cylinder",
                                           "min": lo, "max": hi}
                     ).get_json()["tracked"]["uid"]
        item = cl["items"][0]
        c.post("/api/checklists/%s/track" % cl["uid"],
               json={"item_uid": item["uid"], "tracked_uid": uid})
        return cl, uid

    def _state(self, c, uid):
        return next(x for x in
                    c.get("/api/checklists/trends").get_json()["trends"]
                    if x.get("tracked_uid") == uid)

    def test_a_reading_inside_the_limits_is_in_range(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        cl, uid = self._one(c)
        _write(c, cl, "Nitrogen pressure", 1800, "2026-08-31")
        assert self._state(c, uid)["state"] == "IN RANGE"

    def test_a_reading_below_the_minimum_is_out(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        cl, uid = self._one(c)
        _write(c, cl, "Nitrogen pressure", 400, "2026-08-31")
        assert self._state(c, uid)["state"] == "BELOW MINIMUM"

    def test_a_reading_above_the_maximum_is_out(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        cl, uid = self._one(c)
        _write(c, cl, "Nitrogen pressure", 2400, "2026-08-31")
        assert self._state(c, uid)["state"] == "ABOVE MAXIMUM"

    def test_a_thing_with_no_limits_is_not_judged(self, gw, tmp_path):
        """Still the old rule wherever nobody has set limits: LEM does not
        decide what a good value is."""
        c = _client(gw, tmp_path)
        cl = _round(c, "Opening round", "opening", [_num("Waste bottle")])
        uid = c.post("/api/tracked", json={"name": "Waste bottle"}
                     ).get_json()["tracked"]["uid"]
        c.post("/api/checklists/%s/track" % cl["uid"],
               json={"item_uid": cl["items"][0]["uid"], "tracked_uid": uid})
        _write(c, cl, "Waste bottle", 40, "2026-08-31")
        assert self._state(c, uid)["state"] == "NO LIMITS SET"

    def test_the_limits_ride_along_so_the_chart_can_draw_them(self, gw,
                                                             tmp_path):
        c = _client(gw, tmp_path)
        cl, uid = self._one(c)
        _write(c, cl, "Nitrogen pressure", 1800, "2026-08-31")
        t = self._state(c, uid)
        assert t["min"] == 500 and t["max"] == 2200


class TestConvertingWhatIsAlreadyThere:
    """Ryan: "convert the exist checklists with matching names into those
    objects with history." No reading is moved — the items are pointed at a
    new object and the merge happens on read, so it is reversible."""

    def test_a_dry_run_reports_without_changing_anything(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        _both_rounds(c)
        body = c.post("/api/tracked/convert", json={"dry_run": True}).get_json()
        assert body["would_create"] >= 1
        assert c.get("/api/tracked").get_json()["tracked"] == []

    def test_items_sharing_a_name_become_one_object(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        _both_rounds(c)
        c.post("/api/tracked/convert", json={})
        names = sorted(t["name"] for t in
                       c.get("/api/tracked").get_json()["tracked"])
        assert names == ["Bath temperature", "Helium pressure",
                         "Nitrogen pressure"]

    def test_and_their_history_merges(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        a, b = _both_rounds(c)
        _write(c, a, "Nitrogen pressure", 2100, "2026-08-30")
        _write(c, b, "Nitrogen pressure", 2050, "2026-08-30")
        c.post("/api/tracked/convert", json={})
        t = next(x for x in c.get("/api/checklists/trends").get_json()["trends"]
                 if x["text"] == "Nitrogen pressure")
        assert [p["value"] for p in t["points"]] == [2100.0, 2050.0]
        assert set(t["rounds"]) == {"Opening round", "Closing round"}

    def test_the_match_ignores_case_and_spacing(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        _round(c, "Opening round", "opening", [_num("Nitrogen  Pressure")])
        _round(c, "Closing round", "closing", [_num("nitrogen pressure")])
        c.post("/api/tracked/convert", json={})
        assert len(c.get("/api/tracked").get_json()["tracked"]) == 1

    def test_running_it_twice_creates_nothing_new(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        _both_rounds(c)
        c.post("/api/tracked/convert", json={})
        first = len(c.get("/api/tracked").get_json()["tracked"])
        c.post("/api/tracked/convert", json={})
        assert len(c.get("/api/tracked").get_json()["tracked"]) == first

    def test_text_items_are_left_alone(self, gw, tmp_path):
        c = _client(gw, tmp_path)
        _round(c, "Opening round", "opening",
               [{"text": "Anything unusual", "entry_type": "text"}])
        c.post("/api/tracked/convert", json={})
        assert c.get("/api/tracked").get_json()["tracked"] == []

    def test_it_is_reversible(self, gw, tmp_path):
        """Clearing what an item tracks puts its series back exactly as it
        was, because no reading was ever moved."""
        c = _client(gw, tmp_path)
        a, b = _both_rounds(c)
        _write(c, a, "Nitrogen pressure", 2100, "2026-08-30")
        c.post("/api/tracked/convert", json={})
        for cl in (a, b):
            item = next(i for i in cl["items"]
                        if i["text"] == "Nitrogen pressure")
            c.post("/api/checklists/%s/track" % cl["uid"],
                   json={"item_uid": item["uid"], "tracked_uid": ""})
        trends = c.get("/api/checklists/trends").get_json()["trends"]
        rows = [t for t in trends if t["text"] == "Nitrogen pressure"]
        assert len(rows) == 2, "the two items did not come back apart"
