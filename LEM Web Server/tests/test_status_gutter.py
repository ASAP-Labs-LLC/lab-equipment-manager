"""The status gutter: was this instrument IN CONTROL when that result was made?

Ryan's whiteboard draws an events list with a colour band down the left — a
GREEN band spanning four sample runs, a QC event, then YELLOW, then RED at a QC
that read 500 against a band of about 7.8. The QC events are the TRANSITIONS;
the band says what the instrument's state was WHILE each sample ran.

That is the ISO/IEC 17025 question asked and answered in one place, instead of
an assessor cross-referencing a run report against a QC report by timestamp.

FOUR THINGS THIS FILE HOLDS
---------------------------
1.  **The status is derived from the record, never guessed.** Before the first
    QC in the window the honest answer is UNKNOWN — a bench with no QC yet is
    the grey state this app already refuses to colour in. The seed below
    produces all four of UNKNOWN / RED / GREEN / YELLOW from one instrument, so
    a gutter that returned any single constant status fails here.

2.  **A failing QC does not decay to YELLOW.** YELLOW is a PASS that has aged
    out of the rolling window; RED is a fail, and it stands until another QC
    says otherwise. Getting this backwards turns "this instrument was out of
    spec" into "its QC was a bit old", which is the softer sentence and the
    wrong one.

3.  **A QC event is identifiable as the transition**, and carries its value and
    the band it was judged against — the two numbers a UI needs to mark it and
    an assessor needs to check it.

4.  **It costs zero LabCore ops.** The floor polls, benches poll, and the whole
    performance design of this server is that load does not scale with how many
    things are looking.

The seeds are built from what `qc_log_detail` actually writes — see
`tests/test_qc_control_chart.py::TestTheFixtureIsNotInvented`, which loads the
real function and proves the shape.
"""
import json

import pytest

from labcore_gateway import FakeLabCoreGateway
from models import (STATUS_DEAD, STATUS_GREEN, STATUS_RED, STATUS_SERVICE,
                    STATUS_UNKNOWN, STATUS_YELLOW)
from test_qc_control_chart import qc_detail


EMITTABLE = {STATUS_GREEN, STATUS_YELLOW, STATUS_RED, STATUS_DEAD,
             STATUS_SERVICE, STATUS_UNKNOWN}


class CountingGateway(FakeLabCoreGateway):
    """A gateway that reports how often anything reached LabCore.

    The same guard `test_bench_config.py` and `test_stale_notes.py` use. A
    gutter is exactly the kind of feature that grows an innocent lookup — "just
    fetch the machine's QC window" — and one lookup on a panel the floor opens
    is one LabCore op per screen per click.
    """

    def __init__(self):
        super().__init__()
        self.calls = 0

    def sql(self, *a, **k):
        self.calls += 1
        return super().sql(*a, **k)

    def read_sql(self, *a, **k):
        self.calls += 1
        return super().read_sql(*a, **k)

    def write(self, *a, **k):
        self.calls += 1
        return super().write(*a, **k)


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


@pytest.fixture
def gw():
    return CountingGateway()


@pytest.fixture
def app(gw):
    from web_app import create_app
    application = create_app(gw, authenticator=StubAuth(), secret="s")
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


# ── the lab, over five days ────────────────────────────────────────────────
#
# The band is expected 7.8 +/- 2 * 0.5 -> 6.8 .. 8.8, and the failing QC reads
# 500.0 against it: the whiteboard's own numbers.

BAND = dict(expected=7.8, low=6.8, high=8.8)

TIMELINE = [
    # ts,                    kind, lab_id,  test,         value,  in_spec
    ("2026-08-20T08:00:00", "run", "37244", "",            "9.7", None),
    ("2026-08-20T09:00:00", "qc",  "L-AO25", "Flash Point", 500.0, False),
    ("2026-08-20T10:00:00", "run", "37245", "",            "9.6", None),
    ("2026-08-22T09:00:00", "run", "37246", "",            "9.5", None),
    ("2026-08-23T09:00:00", "qc",  "L-AO25", "Flash Point", 7.8,   True),
    ("2026-08-23T10:00:00", "run", "37247", "",            "9.4", None),
    ("2026-08-23T11:00:00", "run", "37248", "",            "9.3", None),
    ("2026-08-24T20:00:00", "run", "37249", "",            "9.2", None),
]

# Newest first, which is the way the whiteboard reads: the newest run at the
# top and the colour band running down the left beside it.
EXPECTED_STATUSES = [
    STATUS_YELLOW,     # 37249 — 35h after a PASS, the 24h window has expired
    STATUS_GREEN,      # 37248
    STATUS_GREEN,      # 37247
    STATUS_GREEN,      # the QC that read 7.8 — the transition itself
    STATUS_RED,        # 37246 — 48h after a FAIL, and a fail does not go stale
    STATUS_RED,        # 37245
    STATUS_RED,        # the QC that read 500 — the transition itself
    STATUS_UNKNOWN,    # 37244 — before any QC at all
]

FIRST_QC = "2026-08-20T09:00:00"
SECOND_QC = "2026-08-23T09:00:00"


LOG_DDL = ("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
           "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
           "detail TEXT)")


def log(gw, uid, ts, kind, lab_id, test_name, value, detail):
    gw.sql(LOG_DDL)
    gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
           [uid, ts, kind, lab_id, test_name, str(value), json.dumps(detail)])


def run_detail(lab_id, value):
    """EXACTLY what `run_log_detail` puts on a parsed print: `{"values": {...}}`,
    plus `raw`/`corrections` only where an offset was applied. The lab id is the
    log's own COLUMN and is deliberately not repeated in here.

    Note what a run detail does NOT carry: a band, and an `in_spec`. A run has
    no verdict of its own — that is the whole reason the gutter exists — and a
    gutter that read one off a run row would colour the record from the sample
    instead of from the standard.
    """
    return {"values": {"Flash Point": value}}


def seed(gw, uid="pac-flash-2"):
    for ts, kind, lab_id, test, value, in_spec in TIMELINE:
        if kind == "qc":
            detail = qc_detail(BAND["expected"], BAND["low"], BAND["high"],
                               in_spec, operator="Ryan",
                               calibration_id="CAL-2026-1")
        else:
            detail = run_detail(lab_id, value)
        log(gw, uid, ts, kind, lab_id, test, value, detail)


def populate(app, gw, uid="pac-flash-2"):
    """Seed LabCore and let the snapshot read it once, as the poller would."""
    seed(gw, uid)
    app.config["SNAPSHOTS"].refresh()


def gutter(client, uid="pac-flash-2", query=""):
    r = client.get(f"/api/machines/{uid}/status-timeline{query}")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


# ── 1. the status comes off the record ─────────────────────────────────────

class TestTheStatusIsDerivedFromTheQcVerdicts:
    def test_all_four_states_come_out_of_one_instruments_record(
            self, app, gw, client):
        """The anti-constant test. A gutter hard-wired to GREEN, or to the
        machine's status right now, cannot produce this list."""
        populate(app, gw)
        body = gutter(client)
        assert [e["status"] for e in body["events"]] == EXPECTED_STATUSES

    def test_the_events_are_the_instruments_own_and_newest_first(
            self, app, gw, client):
        populate(app, gw)
        events = gutter(client)["events"]
        assert [e["ts"] for e in events] == \
            [ts for ts, *_ in reversed(TIMELINE)]
        assert [e["lab_id"] for e in events] == \
            [lab for _ts, _k, lab, *_ in reversed(TIMELINE)]

    def test_before_the_first_qc_the_answer_is_unknown_not_green(
            self, app, gw, client):
        """A bench with no QC yet is the grey state this app already refuses to
        colour in. Assuming GREEN here would report every run made before the
        first standard as made under control."""
        populate(app, gw)
        oldest = gutter(client)["events"][-1]
        assert oldest["lab_id"] == "37244"
        assert oldest["status"] == STATUS_UNKNOWN
        assert oldest["status_since"] is None

    def test_a_run_carries_the_ts_of_the_qc_that_decided_its_colour(
            self, app, gw, client):
        """Not decoration: it is what lets someone click a sample run and land
        on the QC that says whether it was any good."""
        populate(app, gw)
        by_lab = {e["lab_id"]: e for e in gutter(client)["events"]}
        assert by_lab["37245"]["status_since"] == FIRST_QC
        assert by_lab["37246"]["status_since"] == FIRST_QC
        assert by_lab["37247"]["status_since"] == SECOND_QC
        assert by_lab["37249"]["status_since"] == SECOND_QC

    def test_only_statuses_the_station_module_can_emit_ever_appear(
            self, app, gw, client):
        populate(app, gw)
        seen = {e["status"] for e in gutter(client)["events"]}
        assert seen <= EMITTABLE
        assert seen == {STATUS_UNKNOWN, STATUS_RED, STATUS_GREEN, STATUS_YELLOW}

    def test_every_event_carries_the_sentence_behind_its_colour(
            self, app, gw, client):
        populate(app, gw)
        by_lab = {e["lab_id"]: e for e in gutter(client)["events"]}
        assert by_lab["37244"]["reason"] == "No valid QC data found."
        assert by_lab["37245"]["reason"] == "QC Out of Spec"
        assert by_lab["37247"]["reason"] == "QC Fresh"
        assert by_lab["37249"]["reason"].startswith("QC stale")
        assert "2026-08-23 09:00" in by_lab["37249"]["reason"]


class TestAFailIsNotAStalePass:
    def test_a_red_stands_until_another_qc_says_otherwise(
            self, app, gw, client):
        """37246 is 48 hours after the failing QC and the window is 24. A
        gutter that ran staleness over every verdict would paint it YELLOW,
        downgrading "out of spec" to "a bit old"."""
        populate(app, gw)
        by_lab = {e["lab_id"]: e for e in gutter(client)["events"]}
        assert by_lab["37246"]["status"] == STATUS_RED
        assert by_lab["37246"]["reason"] == "QC Out of Spec"

    def test_a_pass_goes_yellow_exactly_at_the_window(self, app, gw, client):
        """The rolling-window rule this app already runs, applied at the time
        of the EVENT rather than at `now`."""
        populate(app, gw)
        by_lab = {e["lab_id"]: e for e in gutter(client)["events"]}
        assert by_lab["37248"]["status"] == STATUS_GREEN     # +2h
        assert by_lab["37249"]["status"] == STATUS_YELLOW    # +35h


class TestTheQcWindowIsStatedNeverAssumed:
    def test_the_window_used_is_reported_with_the_answer(
            self, app, gw, client):
        """This server has no per-machine QC window in the snapshot, so the
        endpoint uses the same 24h default both engines carry — and SAYS it is
        the default, so nobody reads it as the bench's configured value."""
        populate(app, gw)
        body = gutter(client)
        assert body["qc_expire_hours"] == 24.0
        assert body["qc_expire_source"] == "default"

    def test_a_wider_window_moves_the_boundary_and_says_so(
            self, app, gw, client):
        populate(app, gw)
        body = gutter(client, query="?qc_expire_hours=48")
        assert body["qc_expire_hours"] == 48.0
        assert body["qc_expire_source"] == "request"
        by_lab = {e["lab_id"]: e for e in body["events"]}
        assert by_lab["37249"]["status"] == STATUS_GREEN

    def test_a_narrower_window_moves_it_the_other_way(self, app, gw, client):
        populate(app, gw)
        by_lab = {e["lab_id"]: e
                  for e in gutter(client,
                                  query="?qc_expire_hours=1.5")["events"]}
        assert by_lab["37247"]["status"] == STATUS_GREEN     # +1h
        assert by_lab["37248"]["status"] == STATUS_YELLOW    # +2h

    def test_a_nonsense_window_falls_back_to_the_default(self, app, gw, client):
        populate(app, gw)
        body = gutter(client, query="?qc_expire_hours=banana")
        assert body["qc_expire_hours"] == 24.0
        assert body["qc_expire_source"] == "default"


# ── 2. the transition ──────────────────────────────────────────────────────

class TestAQcEventIsTheTransition:
    def test_a_qc_event_is_marked_as_one_and_a_run_is_not(
            self, app, gw, client):
        populate(app, gw)
        events = gutter(client)["events"]
        assert [e["qc"] for e in events] == \
            [k == "qc" for _ts, k, *_ in reversed(TIMELINE)]
        assert [("transition" in e) for e in events] == \
            [k == "qc" for _ts, k, *_ in reversed(TIMELINE)]

    def test_it_carries_the_value_and_the_band_it_was_judged_against(
            self, app, gw, client):
        populate(app, gw)
        red = [e for e in gutter(client)["events"] if e["ts"] == FIRST_QC][0]
        assert red["transition"]["value"] == 500.0
        assert red["transition"]["band"] == {"low": 6.8, "high": 8.8,
                                             "expected": 7.8}
        assert red["transition"]["in_spec"] is False
        assert red["test_name"] == "Flash Point"

    def test_the_transition_names_both_sides_of_the_boundary(
            self, app, gw, client):
        """`from` is what stood up to that instant and `to` is what the verdict
        establishes, so a UI can draw the band break on either side of the row
        without re-deriving anything."""
        populate(app, gw)
        by_ts = {e["ts"]: e for e in gutter(client)["events"]}
        assert by_ts[FIRST_QC]["transition"] == {
            "from": STATUS_UNKNOWN, "to": STATUS_RED, "in_spec": False,
            "value": 500.0,
            "band": {"low": 6.8, "high": 8.8, "expected": 7.8}}
        assert by_ts[SECOND_QC]["transition"] == {
            "from": STATUS_RED, "to": STATUS_GREEN, "in_spec": True,
            "value": 7.8,
            "band": {"low": 6.8, "high": 8.8, "expected": 7.8}}

    def test_the_qc_events_own_status_is_what_it_establishes(
            self, app, gw, client):
        """The whiteboard puts RED on the row that read 500, not above it. The
        verdict IS the instrument's state at that instant."""
        populate(app, gw)
        by_ts = {e["ts"]: e for e in gutter(client)["events"]}
        assert by_ts[FIRST_QC]["status"] == STATUS_RED
        assert by_ts[SECOND_QC]["status"] == STATUS_GREEN
        assert by_ts[FIRST_QC]["status_since"] == FIRST_QC

    def test_a_qc_row_whose_detail_will_not_parse_is_unknown_not_a_pass(
            self, app, gw, client):
        """`in_spec` is tri-state everywhere else in this tree and it is
        tri-state here. A formatting problem must not read as a verdict."""
        seed(gw)
        gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
               ["pac-flash-2", "2026-08-25T09:00:00", "qc", "L-AO25",
                "Flash Point", "7.9", "{not json at all"])
        log(gw, "pac-flash-2", "2026-08-25T10:00:00", "run", "37250", "",
            "9.1", run_detail("37250", "9.1"))
        app.config["SNAPSHOTS"].refresh()
        by_lab = {e["lab_id"]: e for e in gutter(client)["events"]}
        broken = [e for e in gutter(client)["events"]
                  if e["ts"] == "2026-08-25T09:00:00"][0]
        assert broken["status"] == STATUS_UNKNOWN
        assert broken["transition"]["in_spec"] is None
        assert broken["transition"]["band"] is None
        assert by_lab["37250"]["status"] == STATUS_UNKNOWN

    def test_a_pm_completion_is_an_event_and_never_a_transition(
            self, app, gw, client):
        """The `_is_qc_row` lesson from the other side: a maintenance record
        sharing the machine and the test name once overwrote a certificate's
        band with its own (0 - 0.001)."""
        seed(gw)
        log(gw, "pac-flash-2", "2026-08-23T12:00:00", "pm", "", "Flash Point",
            "", {"task": "Annual service", "low": 0.0, "high": 0.001,
                 "expected": 0.0005, "in_spec": False, "by": "Ryan"})
        app.config["SNAPSHOTS"].refresh()
        pm = [e for e in gutter(client)["events"]
              if e["ts"] == "2026-08-23T12:00:00"][0]
        assert pm["kind"] == "pm"
        assert pm["qc"] is False
        assert "transition" not in pm
        # And it did not knock the instrument out of the state the QC set.
        assert pm["status"] == STATUS_GREEN

    def test_another_machines_qc_never_colours_this_one(
            self, app, gw, client):
        populate(app, gw)
        log(gw, "multitek-ns", "2026-08-24T21:00:00", "qc", "L-AO25",
            "Sulfur", 500.0,
            qc_detail(10.0, 9.0, 11.0, False, operator="Ryan",
                      calibration_id="CAL-2026-1"))
        app.config["SNAPSHOTS"].refresh()
        body = gutter(client)
        assert [e["status"] for e in body["events"]] == EXPECTED_STATUSES
        assert all(e["machine_uid"] == "pac-flash-2" for e in body["events"])


class TestEveryAssignedTestHasToBeInSpec:
    def test_one_failing_test_holds_the_instrument_red(self, app, gw, client):
        """A machine runs more than one QC. The engine already says RED if ANY
        assigned test is out of spec, and the gutter is the same rule read
        backwards through time — otherwise a passing Pour Point would paint
        over a failing Flash Point."""
        log(gw, "m2", "2026-08-20T09:00:00", "qc", "L-AO25", "Flash Point",
            500.0, qc_detail(7.8, 6.8, 8.8, False, operator="Ryan",
                             calibration_id="CAL-1"))
        log(gw, "m2", "2026-08-20T10:00:00", "qc", "L-PP", "Pour Point",
            -19.0, qc_detail(-18.3, -24.7, -11.9, True, operator="Ryan",
                             calibration_id="CAL-1"))
        log(gw, "m2", "2026-08-20T11:00:00", "run", "37300", "", "9.7",
            run_detail("37300", "9.7"))
        app.config["SNAPSHOTS"].refresh()
        events = gutter(client, "m2")["events"]
        by_lab = {e["lab_id"]: e for e in events}
        assert by_lab["37300"]["status"] == STATUS_RED
        assert by_lab["37300"]["status_since"] == "2026-08-20T09:00:00"
        # The passing Pour Point is still a transition, and its own row is
        # RED — the instrument was out of control when it was run.
        pour = [e for e in events if e["test_name"] == "Pour Point"][0]
        assert pour["transition"]["in_spec"] is True
        assert pour["status"] == STATUS_RED

    def test_the_failing_test_clears_when_it_passes_again(
            self, app, gw, client):
        log(gw, "m3", "2026-08-20T09:00:00", "qc", "L-AO25", "Flash Point",
            500.0, qc_detail(7.8, 6.8, 8.8, False, operator="Ryan",
                             calibration_id="CAL-1"))
        log(gw, "m3", "2026-08-20T10:00:00", "qc", "L-AO25", "Flash Point",
            7.8, qc_detail(7.8, 6.8, 8.8, True, operator="Ryan",
                           calibration_id="CAL-1"))
        log(gw, "m3", "2026-08-20T11:00:00", "run", "37301", "", "9.7",
            run_detail("37301", "9.7"))
        app.config["SNAPSHOTS"].refresh()
        by_lab = {e["lab_id"]: e for e in gutter(client, "m3")["events"]}
        assert by_lab["37301"]["status"] == STATUS_GREEN
        assert by_lab["37301"]["status_since"] == "2026-08-20T10:00:00"


# ── 3. what it costs ───────────────────────────────────────────────────────

class TestServingTheGutterNeverTouchesLabCore:
    def test_thirty_requests_cost_nothing(self, app, gw, client):
        populate(app, gw)
        before = gw.calls
        for _ in range(30):
            assert len(gutter(client)["events"]) == len(TIMELINE)
        assert gw.calls == before

    def test_the_answer_says_where_it_came_from_and_how_old_it_is(
            self, app, gw, client):
        """`snapshot_age_seconds` is not decoration anywhere else in this app
        and it is not here: a gutter served from a stale snapshot is a
        compliance answer with an unstated as-of."""
        populate(app, gw)
        body = gutter(client)
        assert body["source"] == "snapshot"
        assert isinstance(body["snapshot_age_seconds"], float)
        assert body["snapshot_age_seconds"] >= 0

    def test_the_window_it_covers_is_stated(self, app, gw, client):
        """The snapshot holds the newest rows for the whole lab, so a quiet
        instrument's gutter can be clipped. Saying so is the difference between
        "nothing else happened" and "nothing else is in this answer"."""
        populate(app, gw)
        body = gutter(client)
        assert body["complete"] is True
        assert body["covers_from"] == TIMELINE[0][0]


class TestAFailedReadIsNeverAnEmptyGutter:
    def test_labcore_down_reports_a_failure_not_a_quiet_instrument(self):
        """An empty gutter reads as "this instrument has done nothing", which
        on a 17025 panel is a statement about the record."""
        from web_app import create_app

        class Broken(FakeLabCoreGateway):
            def read_sql(self, sql, args=None, **kw):
                if "lem_machine_log" in sql:
                    return {"error": "HTTPSConnectionPool: Read timed out"}
                return super().read_sql(sql, args, **kw)

        broken = Broken()
        application = create_app(broken, authenticator=StubAuth(), secret="s")
        application.config["TESTING"] = True
        r = application.test_client().get(
            "/api/machines/pac-flash-2/status-timeline")
        assert r.status_code in (502, 503)
        body = r.get_json()
        assert body["error"] and body["retry"] is True
        assert "events" not in body

    def test_an_instrument_with_no_history_is_an_empty_list_and_a_200(
            self, app, gw, client):
        """Empty because nothing was logged is a real answer, and different
        from the one above."""
        populate(app, gw)
        body = gutter(client, "nobody-here")
        assert body["events"] == []
        assert body["covers_from"] is None
