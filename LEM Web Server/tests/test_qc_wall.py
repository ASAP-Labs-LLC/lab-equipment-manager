"""The QC wall — every instrument's control chart, on a screen nobody touches.

Ryan, 28 Aug 2026: *"another tab beneath logs called QC, where it just shows
the QC history graph but for all the machines, design it to run like a literal
monitor, for viewing far away, and just being locked on that screen."*

Three things follow from "a monitor", and they are what this file is about.

**It polls forever, so it may not cost LabCore anything.** A page left open on a
wall for a year, refreshing, is the exact load pattern the snapshot design
exists to prevent — and unlike the floor, this one needs QC HISTORY, which is
deep. It reads from the local log mirror, which already holds every
`lem_machine_log` row and is refreshed once every five minutes. Five minutes is
invisible on a QC chart whose points are hours apart.

**Nobody is standing there to interpret it.** So the wall may never show a
state it cannot justify: a failed read must look like a failed read and not
like a lab in control, and a series with too few points to judge must say so
rather than drawing a confident line through three dots.

**The worst thing it can do is look fine.** Everything else is a nuisance; a
green wall over an out-of-control instrument is the failure this whole product
exists to prevent. Most of what is asserted below is that one thing, from
several directions.
"""

import json

import pytest

import snapshot_service
from labcore_gateway import FakeLabCoreGateway
from web_app import create_app

FLASH = "pac-flash-1"
VISC = "viscocity"


def _qc(gw, machine_uid, test_name, value, ts, in_spec=True,
        low=61.6, high=65.8, expected=63.7, operator="ryan"):
    gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
           "test_name, value, detail) VALUES (?, ?, 'qc', 'STD-1', ?, ?, ?)",
           [machine_uid, ts, test_name, str(value),
            json.dumps({"low": low, "high": high, "expected": expected,
                        "in_spec": in_spec, "operator": operator})])


def _series(gw, machine_uid, test_name, values, day=1, **kw):
    for i, v in enumerate(values):
        _qc(gw, machine_uid, test_name, v,
            "2026-08-%02dT09:00:00" % (day + i), **kw)


@pytest.fixture
def gw():
    g = FakeLabCoreGateway()
    snapshot_service.SnapshotService(g).ensure_schema()
    return g


def _app(gw, tmp_path, fill=True):
    app = create_app(gw, secret="t", documents_root=str(tmp_path))
    app.config.update(TESTING=True)
    if fill:
        app.config["LOG_MIRROR"].refresh()
    return app


def _client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = "ryan"
    return c


def _wall(gw, tmp_path, **kw):
    return _client(_app(gw, tmp_path, **kw)).get("/api/qc-wall")


class TestItShowsEveryInstrumentsQc:
    def test_one_entry_per_machine_and_test(self, gw, tmp_path):
        _series(gw, FLASH, "Flash Point", [63.5, 63.8, 63.6])
        _series(gw, VISC, "Viscosity", [2.9, 2.95, 2.92],
                low=2.76, high=3.2, expected=2.98)
        body = _wall(gw, tmp_path).get_json()
        got = {(s["machine_uid"], s["test_name"]) for s in body["series"]}
        assert got == {(FLASH, "Flash Point"), (VISC, "Viscosity")}

    def test_a_machine_with_several_methods_gets_several_charts(self, gw,
                                                                tmp_path):
        """One instrument, two methods, two charts. Collapsing them to the
        worst would hide the method that is fine and the method that is not."""
        _series(gw, "optimpp-1", "Cloud Point", [-12.1, -12.4, -12.2],
                low=-16.0, high=-12.0, expected=-14.0)
        _series(gw, "optimpp-1", "Pour Point", [-20.4, -20.1, -20.6],
                low=-25.0, high=-17.0, expected=-21.0)
        body = _wall(gw, tmp_path).get_json()
        mine = [s for s in body["series"] if s["machine_uid"] == "optimpp-1"]
        assert len(mine) == 2

    def test_each_chart_carries_its_points_and_its_band(self, gw, tmp_path):
        _series(gw, FLASH, "Flash Point", [63.5, 63.8, 63.6])
        s = _wall(gw, tmp_path).get_json()["series"][0]
        assert len(s["points"]) == 3
        assert s["pass_band"]["low"] == 61.6 and s["pass_band"]["high"] == 65.8

    def test_the_instrument_is_named_not_just_uid(self, gw, tmp_path):
        """Nobody reads a uid from across a room."""
        # `lem_machine_status` is where the title lives and what the floor's
        # snapshot already carries — which is why the wall reads it from there
        # rather than paying LabCore for a name.
        gw.sql("INSERT INTO lem_machine_status (machine_uid, title, status, "
               "reason, updated_at) VALUES (?, 'PAC Flash 1', 'GREEN', '', '')",
               [FLASH])
        _series(gw, FLASH, "Flash Point", [63.5, 63.8])
        s = _wall(gw, tmp_path).get_json()["series"][0]
        assert s["title"] == "PAC Flash 1"


class TestWhatIsWrongComesFirst:
    """Nobody scrolls a wall display. If it does not fit, the thing that fits
    has to be the thing that matters."""

    def test_an_out_of_spec_series_sorts_above_a_healthy_one(self, gw,
                                                            tmp_path):
        _series(gw, "healthy", "Flash Point", [63.5, 63.6, 63.7])
        _series(gw, "bad", "Flash Point", [63.5, 63.6, 70.2], in_spec=False)
        order = [s["machine_uid"] for s in _wall(gw, tmp_path).get_json()["series"]]
        assert order.index("bad") < order.index("healthy")

    def test_every_series_says_its_state_in_one_word(self, gw, tmp_path):
        _series(gw, FLASH, "Flash Point", [63.5, 63.6, 63.7])
        s = _wall(gw, tmp_path).get_json()["series"][0]
        assert s["state"] in ("IN CONTROL", "OUT OF SPEC", "TOO FEW", "STALE")

    def test_a_series_with_too_few_points_says_so_rather_than_looking_fine(
            self, gw, tmp_path):
        """Three dots and a confident line is the most dangerous chart on the
        wall, because it reads exactly like a chart that means something."""
        _qc(gw, "lonely", "Flash Point", 63.5, "2026-08-01T09:00:00")
        s = [x for x in _wall(gw, tmp_path).get_json()["series"]
             if x["machine_uid"] == "lonely"][0]
        assert s["state"] == "TOO FEW"


class TestItCannotShowAGoodWallForABadReason:
    """The one that turns into a finding."""

    def test_a_failed_read_is_not_an_empty_wall(self, gw, tmp_path):
        _series(gw, FLASH, "Flash Point", [63.5, 63.6, 63.7])
        app = _app(gw, tmp_path, fill=False)

        def blind(sql, args=None, **kw):
            if "lem_machine_log" in sql:
                return {"error": "LabCore is busy", "busy": True}
            return {"ok": True, "rows": []}

        gw.read_sql = blind
        r = _client(app).get("/api/qc-wall")
        assert r.status_code >= 400, r.get_json()

    def test_a_lab_with_no_qc_at_all_says_that_in_words(self, gw, tmp_path):
        """An empty grid reads as "everything is fine". It is not the same
        sentence as "nothing is being checked", and on this screen the second
        one is the finding."""
        body = _wall(gw, tmp_path).get_json()
        assert body["series"] == []
        assert str(body.get("nothing_checked", "")).strip()

    def test_the_answer_says_how_old_the_data_is(self, gw, tmp_path):
        """Read from a five-minute mirror. Invisible on a QC chart, and it may
        not be silent — a wall that has quietly stopped updating looks exactly
        like a wall showing a calm lab."""
        _series(gw, FLASH, "Flash Point", [63.5, 63.6])
        body = _wall(gw, tmp_path).get_json()
        assert body.get("as_of")


class TestItDoesNotCostLabCoreAnything:
    """A page left open on a wall, refreshing forever. The mirror exists so
    that costs nothing; reading LabCore per refresh would put a permanent
    poller on the queue the benches write through."""

    def test_a_refresh_reads_the_mirror_not_labcore(self, gw, tmp_path):
        _series(gw, FLASH, "Flash Point", [63.5, 63.6, 63.7])
        app = _app(gw, tmp_path)
        hits = {"n": 0}
        real = gw.read_sql

        def counted(sql, args=None, **kw):
            if "lem_machine_log" in sql:
                hits["n"] += 1
            return real(sql, args, **kw)

        gw.read_sql = counted
        body = _client(app).get("/api/qc-wall").get_json()
        assert body["series"], "nothing drawn"
        assert hits["n"] == 0, "the wall read LabCore; the mirror is why it need not"

    def test_an_unfilled_mirror_falls_back_rather_than_showing_nothing(
            self, gw, tmp_path):
        _series(gw, FLASH, "Flash Point", [63.5, 63.6, 63.7])
        body = _client(_app(gw, tmp_path, fill=False)).get(
            "/api/qc-wall").get_json()
        assert body["series"], "a cold mirror emptied the wall"


class TestThePageIsReachableAndIsAMonitor:
    def test_the_qc_page_is_served(self, gw, tmp_path):
        assert _client(_app(gw, tmp_path)).get("/qc").status_code == 200

    def test_it_is_in_the_nav_under_logs(self, gw, tmp_path):
        body = _client(_app(gw, tmp_path)).get("/floor").get_data(as_text=True)
        assert '/qc' in body
        assert body.index('/logs') < body.index('/qc'), (
            "the QC tab has to sit beneath Logs, which is where it was asked for")

    def test_the_page_refreshes_itself(self, gw, tmp_path):
        """Nobody presses anything on a wall display."""
        page = _client(_app(gw, tmp_path)).get("/qc").get_data(as_text=True)
        assert "setInterval" in page

    def test_the_routes_are_registered(self, gw, tmp_path):
        rules = {str(r) for r in _app(gw, tmp_path).url_map.iter_rules()}
        assert "/qc" in rules and "/api/qc-wall" in rules
