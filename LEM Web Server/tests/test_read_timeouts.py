"""Every read in this app waits behind LabCore's write queue.

`read_sql` POSTs to `/api/queue/write`, so the vendored client's 8s read allowance is
not a measure of how slow the query is — it is a bet on how busy the lab is. Measured
live: the queue bursts to 81 pending and throughput drops to 0.1 ops/sec, while the
query itself takes 0.12s.

So the gateway raises the default. It is the layer that knows this about LabCore, and
fixing it there covers every call site instead of leaving each one to guess.

The background snapshot asks for even longer (45s) because nobody is waiting on it.
Request-path reads get less, because a person is.
"""
import pytest


class Recorder:
    """Stands in for the vendored client, recording the timeout it was handed."""

    def __init__(self):
        self.timeouts = []

    def read_sql(self, sql, args=None, timeout=None, **kw):
        self.timeouts.append(timeout)
        return {"rows": []}

    def sql(self, sql, args=None, timeout=None, **kw):
        self.timeouts.append(timeout)
        return {"ok": True}


@pytest.fixture
def gw():
    from labcore_gateway import HttpLabCoreGateway
    g = HttpLabCoreGateway(base_url="https://labcore.invalid")
    g._client = Recorder()
    return g


class TestTheGatewayRaisesTheDefault:
    def test_a_read_with_no_timeout_gets_a_generous_one(self, gw):
        from labcore_gateway import READ_TIMEOUT
        gw.read_sql("SELECT 1")
        assert gw._client.timeouts == [READ_TIMEOUT]

    def test_it_is_longer_than_the_vendored_default(self, gw):
        import labcore_client
        from labcore_gateway import READ_TIMEOUT
        assert READ_TIMEOUT > getattr(labcore_client, "DEFAULT_TIMEOUT", 8)

    def test_a_caller_can_still_ask_for_longer(self, gw):
        gw.read_sql("SELECT 1", timeout=45.0)
        assert gw._client.timeouts == [45.0]

    def test_a_caller_can_still_ask_for_shorter(self, gw):
        """A liveness probe must not inherit a patient timeout."""
        gw.read_sql("SELECT 1", timeout=2.0)
        assert gw._client.timeouts == [2.0]

    def test_writes_are_left_alone(self, gw):
        """A write that is queued has already been accepted; stretching its
        timeout only makes an operator wait longer for a click."""
        gw.sql("CREATE TABLE IF NOT EXISTS x (a TEXT)")
        assert gw._client.timeouts == [None]

    def test_the_snapshot_still_asks_for_more(self):
        from labcore_gateway import READ_TIMEOUT as GATEWAY
        from snapshot_service import READ_TIMEOUT as SNAPSHOT
        assert SNAPSHOT > GATEWAY, \
            "the background poller should be the most patient reader"


class TestAFailedReadIsNotAnEmptyLog:
    """`[] if res.get("error") else rows` reads a failed query as "nothing
    happened". On the logs page that is a lab with no history, which is worse than
    an error — it is a confident wrong answer."""

    @pytest.fixture
    def client(self):
        from labcore_gateway import FakeLabCoreGateway
        from web_app import create_app

        class Auth:
            def login(self, u, p):
                return ("kaden", "t", "")

            def logout(self, t):
                pass

        g = FakeLabCoreGateway()
        g.sql("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
              "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
              "detail TEXT)")
        g.sql("INSERT INTO lem_machine_log VALUES "
              "('m1','2026-08-03T09:00:00','run','1','CP','-7.4','{}')")
        app = create_app(g, authenticator=Auth(), secret="s")
        app.config["TESTING"] = True
        return g, app.test_client()

    def test_a_readable_log_reports_no_error(self, client):
        _gw, c = client
        body = c.get("/api/logs").get_json()
        assert body["events"]
        assert not body.get("error")

    def test_a_failed_read_says_so_instead_of_showing_nothing(self, client):
        gw, c = client
        gw.read_sql = lambda *a, **k: {"error": "read timed out"}
        body = c.get("/api/logs").get_json()
        assert body.get("error"), "an unreadable log looked like an empty one"

    def test_it_still_returns_a_usable_shape(self, client):
        """The page must not crash on the error path."""
        gw, c = client
        gw.read_sql = lambda *a, **k: {"error": "read timed out"}
        body = c.get("/api/logs").get_json()
        assert body["events"] == [] and isinstance(body.get("kinds"), list)

    def test_the_page_shows_the_message(self):
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "templates" / "logs.html").read_text(encoding="utf-8")
        assert "error" in src
