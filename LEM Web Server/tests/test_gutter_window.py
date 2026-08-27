"""An empty status gutter must be a statement about the WINDOW.

Ryan, 27 Aug: *"The Agilent GC doesn't show events for some reason, but other
equipment does."* Measured against the live lab, the API is healthy and the
instrument is the busiest one in it:

    lem_machine_log rows            26,106  (most of any instrument)
    log read, three runs            0.10 – 0.84 s
    GET /api/equipment/<uid>/history   200, 200 entries

What is wrong is the gutter's window. `EVENT_LIMIT` is **60 rows for the whole
lab**, and on this floor that reaches back about four hours — Eraspec NIR alone
holds 29 of the 60. Agilent's newest event is fourteen hours old, so it owns
**none** of them, and the panel said:

    Nothing is recorded against this equipment in this window.

`api_status_timeline`'s own docstring already names this as the thing not to
do: *"'nothing else happened' and 'nothing else is in this answer' are
different sentences and only one of them is a statement about the record."*
The payload was meant to carry that distinction in `complete` and
`covers_from`, and one line stops it:

    covers_from = events[-1]["ts"] if events else None

`events` is THIS INSTRUMENT'S slice of the window. When the instrument owns
nothing, the horizon collapses to `None` — so the one field that could say
"this answer only reaches back to 09:58" goes silent in exactly the case it
exists for. And when the instrument does own rows, `covers_from` reports how
far back ITS OWN events go, which is not what the window covers either: an
instrument with two events an hour apart reports a one-hour horizon over a
four-hour window, and a reader takes that as the limit of the record.

`covers_from` is a property of the window. It comes off the window.
"""

import pytest

import snapshot_service
from labcore_gateway import FakeLabCoreGateway
from snapshot_service import EVENT_LIMIT
from web_app import create_app

BUSY = "eraspec-nir"
QUIET = "agilent-gc-1"


def _log(gw, machine_uid, ts, kind="run", test_name="Flash Point"):
    gw.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
           "test_name, value, detail) VALUES (?, ?, ?, '', ?, '1.0', '{}')",
           [machine_uid, ts, kind, test_name])


@pytest.fixture
def gw():
    g = FakeLabCoreGateway()
    snapshot_service.SnapshotService(g).ensure_schema()
    return g


def _client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = "ryan"
    return c


def _app(gw):
    application = create_app(gw, secret="t")
    application.config.update(TESTING=True)
    return application


class TestTheSnapshotCannotAnswerForAClippedOutInstrument:
    """The condition the route was missing.

    It asked "does a snapshot exist", never "does the snapshot have anything to
    say about THIS instrument". Those are different questions the moment the
    window is lab-wide, and on this floor they diverge for eleven of sixteen
    instruments at any given moment.
    """

    @pytest.fixture
    def crowded(self, gw):
        # The quiet one reported yesterday, and then stopped.
        for i in range(4):
            _log(gw, QUIET, "2026-08-26T0%d:00:00" % i)
        # The busy one has filled every slot in the window since.
        for i in range(EVENT_LIMIT + 20):
            _log(gw, BUSY, "2026-08-27T09:%02d:00" % (i % 60))
        return gw

    def test_the_shared_window_really_does_exclude_it(self, crowded):
        """The premise, asserted rather than assumed: with the window full of
        a neighbour's rows, none of this instrument's are in it. If this ever
        stops being true the tests below are testing nothing."""
        body = _client(_app(crowded)).get(
            "/api/machines/%s/status-timeline" % BUSY).get_json()
        assert body["source"] == "snapshot"
        assert len(body["events"]) == EVENT_LIMIT, (
            "the busy instrument should own the entire shared window")

    def test_the_quiet_instrument_is_answered_anyway(self, crowded):
        """Was: an empty panel on the instrument with the most to show."""
        body = _client(_app(crowded)).get(
            "/api/machines/%s/status-timeline" % QUIET).get_json()
        assert body["events"], "still blank"
        assert all(e["machine_uid"] == QUIET for e in body["events"])

    def test_its_horizon_is_its_own_read_not_the_shared_window(self, crowded):
        """Answered off a per-machine read, so what it covers is what that read
        covers — yesterday, not the four hours the shared window holds."""
        body = _client(_app(crowded)).get(
            "/api/machines/%s/status-timeline" % QUIET).get_json()
        assert body["covers_from"].startswith("2026-08-26"), body["covers_from"]

    def test_and_that_read_was_complete(self, crowded):
        """Four rows out of a limit of sixty: nothing was cut, and the panel
        may say so."""
        body = _client(_app(crowded)).get(
            "/api/machines/%s/status-timeline" % QUIET).get_json()
        assert body["complete"] is True


class TestAClippedOutInstrumentIsFetchedRatherThanReportedEmpty:
    """The honest message is not the whole fix.

    Saying "this window does not reach back far enough" about the busiest
    instrument in the lab is true, and still leaves the panel blank on the one
    piece of equipment with the most to show. The route already knows how to
    read one instrument's events directly — that is its cold path, for when the
    snapshot has never built. This is the same read, on the same condition
    restated: the snapshot cannot answer for this machine.

    It costs ONE LabCore read, and only when somebody opens the record of an
    instrument that has been quiet. `select()` already reads this equipment's
    history on the same click, so the marginal cost of the panel going from
    blank to correct is one read on a screen that was already making one.

    Deliberately NOT done instead: partitioning the snapshot's event arm per
    machine. That keeps the zero-op rule, but `PARTITION BY machine_uid` cannot
    use either existing index — measured 0.2s against a 42k-row table today,
    and it scans the whole table every twelve seconds forever. LabCore
    interrupts any read over 8s, so that is a cliff rather than a slope, and it
    would need a new index on the production database to avoid.
    """

    @pytest.fixture
    def crowded(self, gw):
        for i in range(4):
            _log(gw, QUIET, "2026-08-26T0%d:00:00" % i)
        for i in range(EVENT_LIMIT + 20):
            _log(gw, BUSY, "2026-08-27T09:%02d:00" % (i % 60))
        return gw

    def test_the_quiet_instruments_own_events_are_served(self, crowded):
        client = _client(_app(crowded))
        body = client.get(
            "/api/machines/%s/status-timeline" % QUIET).get_json()
        assert body["events"], (
            "the busiest instrument in the lab still shows an empty panel")
        assert all(e["machine_uid"] == QUIET for e in body["events"])

    def test_and_the_answer_says_it_did_not_come_from_the_snapshot(
            self, crowded):
        """A reader has to be able to tell which of the two reads answered —
        one is 12 seconds stale and free, the other is current and costs a
        LabCore op."""
        client = _client(_app(crowded))
        body = client.get(
            "/api/machines/%s/status-timeline" % QUIET).get_json()
        assert body["source"] == "labcore", body["source"]

    def test_an_instrument_inside_the_window_does_not_pay_for_a_read(
            self, crowded):
        """The common path stays free. Only a clipped-out instrument falls
        through, or every open record costs LabCore a read."""
        client = _client(_app(crowded))
        body = client.get(
            "/api/machines/%s/status-timeline" % BUSY).get_json()
        assert body["source"] == "snapshot", body["source"]

    def test_a_truly_silent_instrument_is_not_fetched_forever(self, gw):
        """An instrument with no rows at all must not turn every panel open
        into a LabCore read that comes back empty every time. The window was
        not full here, so there is nothing it could be hiding."""
        _log(gw, BUSY, "2026-08-27T09:00:00")
        client = _client(_app(gw))
        body = client.get(
            "/api/machines/%s/status-timeline" % QUIET).get_json()
        assert body["source"] == "snapshot", body["source"]
        assert body["events"] == []


class TestTheHorizonIsTheWindowEvenWhenTheInstrumentIsBusy:
    def test_two_sparse_events_do_not_report_a_two_event_horizon(self, gw):
        """An instrument with two events an hour apart inside a four-hour
        window reported a one-hour horizon. A reader takes that as the limit of
        the record, and it is not."""
        _log(gw, QUIET, "2026-08-27T11:00:00")
        _log(gw, QUIET, "2026-08-27T12:00:00")
        for i in range(EVENT_LIMIT + 5):
            _log(gw, BUSY, "2026-08-27T09:%02d:00" % (i % 60))
        client = _client(_app(gw))
        body = client.get(
            "/api/machines/%s/status-timeline" % QUIET).get_json()
        assert body["events"], "this instrument should own some of the window"
        # Its own oldest is 11:00; the window reaches back to 09:xx.
        assert body["covers_from"] < "2026-08-27T11:00:00", body["covers_from"]


class TestAGenuinelyQuietLabStillReadsAsQuiet:
    """The honest empty case has to survive the fix — otherwise every silent
    instrument grows a caveat about a window that was never short."""

    def test_a_window_that_was_not_full_is_complete(self, gw):
        _log(gw, BUSY, "2026-08-27T09:00:00")
        client = _client(_app(gw))
        body = client.get(
            "/api/machines/%s/status-timeline" % QUIET).get_json()
        assert body["events"] == []
        assert body["complete"] is True

    def test_an_empty_lab_reports_no_horizon_rather_than_a_wrong_one(self, gw):
        client = _client(_app(gw))
        body = client.get(
            "/api/machines/%s/status-timeline" % QUIET).get_json()
        assert body["complete"] is True
        assert not body["covers_from"]
