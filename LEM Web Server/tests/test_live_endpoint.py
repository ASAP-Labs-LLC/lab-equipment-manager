"""`POST /api/live` — the bench telling the floor about itself, directly.

The rule that makes this safe to add: **the push path never touches LabCore.**
No read, no write, and specifically no `refresh_soon()`. Waking the snapshot on
every ping would rebuild "LabCore load scales with how many benches are running",
which is the same bug that was fixed for screens on 2026-08-03 — just keyed on
bench count instead of screen count. `CountingGateway` is here to prove it.
"""
import pytest

from labcore_gateway import FakeLabCoreGateway
from live_presence import LivePresence


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


class CountingGateway(FakeLabCoreGateway):
    """A gateway that reports how often anything reached LabCore."""

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


@pytest.fixture
def gw():
    return CountingGateway()


@pytest.fixture
def app(gw):
    from web_app import create_app
    application = create_app(gw, authenticator=StubAuth(), secret="s",
                             live=LivePresence(), live_token="test-token")
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def push(client, **over):
    body = {"machine_uid": "pac-flash-2", "status": "GREEN", "reason": "",
            "at": "2026-08-05T14:02:11", "interval_seconds": 30}
    body.update(over)
    return client.post("/api/live", json=body,
                       headers={"X-LEM-Token": "test-token"})


class TestThePushIsAccepted:
    def test_a_valid_push_is_taken(self, client, app):
        assert push(client).status_code == 204
        assert app.config["LIVE"].get("pac-flash-2")["status"] == "GREEN"

    def test_a_machine_the_floor_has_never_heard_of_is_still_taken(self, client):
        """The floor only draws machines it knows, so an unknown uid is inert
        rather than an error a bench has to handle."""
        assert push(client, machine_uid="brand-new").status_code == 204

    def test_the_parse_details_come_through(self, client, app):
        push(client, last_parse_at="2026-08-05T14:02:10", lab_id="L-1234")
        entry = app.config["LIVE"].get("pac-flash-2")
        assert entry["lab_id"] == "L-1234"


class TestTheTokenIsChecked:
    def test_no_token_is_refused(self, client):
        response = client.post("/api/live", json={"machine_uid": "m1"})
        assert response.status_code == 401

    def test_a_wrong_token_is_refused(self, client):
        response = client.post("/api/live", json={"machine_uid": "m1"},
                               headers={"X-LEM-Token": "guess"})
        assert response.status_code == 401

    def test_a_refused_push_stores_nothing(self, client, app):
        client.post("/api/live", json={"machine_uid": "m1"},
                    headers={"X-LEM-Token": "guess"})
        assert app.config["LIVE"].get("m1") is None

    def test_the_token_is_never_echoed_back(self, client):
        response = client.post("/api/live", json={"machine_uid": "m1"},
                               headers={"X-LEM-Token": "guess"})
        assert "test-token" not in response.get_data(as_text=True)


class TestABadBodyIsRefused:
    def test_a_body_that_is_not_an_object(self, client):
        response = client.post("/api/live", json=["nope"],
                               headers={"X-LEM-Token": "test-token"})
        assert response.status_code == 400

    def test_a_body_that_is_not_json_at_all(self, client):
        response = client.post("/api/live", data="garbage",
                               headers={"X-LEM-Token": "test-token"})
        assert response.status_code == 400

    def test_a_push_with_no_machine(self, client):
        response = client.post("/api/live", json={"status": "GREEN"},
                               headers={"X-LEM-Token": "test-token"})
        assert response.status_code == 400


class TestThePushNeverTouchesLabCore:
    def test_a_push_costs_zero_labcore_operations(self, client, gw):
        gw.calls = 0
        for _ in range(20):
            push(client)
        assert gw.calls == 0, (
            "the push path reached LabCore — that makes LabCore load scale with "
            "the number of running benches")

    def test_a_refused_push_costs_nothing_either(self, client, gw):
        gw.calls = 0
        client.post("/api/live", json={"machine_uid": "m1"},
                    headers={"X-LEM-Token": "guess"})
        assert gw.calls == 0


class TestTheTokenIsSettledWithoutAnyone:
    def test_a_server_given_no_token_still_has_one(self, gw):
        from web_app import create_app
        application = create_app(gw, authenticator=StubAuth(), secret="s")
        assert len(application.config["LIVE_TOKEN"]) >= 32

    def test_a_server_given_no_token_refuses_a_blank_one(self, gw):
        from web_app import create_app
        application = create_app(gw, authenticator=StubAuth(), secret="s")
        application.config["TESTING"] = True
        response = application.test_client().post(
            "/api/live", json={"machine_uid": "m1"},
            headers={"X-LEM-Token": ""})
        assert response.status_code == 401


def seed_machine(gw, uid="pac-flash-2", title="PAC Flash 2", status="RED",
                 reason="Flash Point out of spec", at="2026-08-05T13:00:00"):
    gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
           "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
           "reason TEXT, updated_at TEXT)")
    gw.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
           [uid, title, status, reason, at])


def machines_of(client):
    body = client.get("/api/machines?fresh=1").get_json()
    return {m["machine_uid"]: m for m in body.get("machines") or []}


class TestTheFloorPrefersTheLiveRoad:
    """One deterministic rule: live entry if one is fresh, else the record."""

    def test_a_live_status_replaces_the_recorded_one(self, client, gw):
        seed_machine(gw, status="RED", reason="Flash Point out of spec")
        push(client, status="GREEN", reason="")

        machine = machines_of(client)["pac-flash-2"]

        assert machine["status"] == "GREEN"
        assert machine["reason"] == ""
        assert machine["live"] is True

    def test_the_dot_colour_follows_the_live_status(self, client, gw):
        """Overlaying the status without its colour would leave a green machine
        drawn red."""
        seed_machine(gw, status="RED")
        push(client, status="GREEN")

        machine = machines_of(client)["pac-flash-2"]
        red = "#f85b5b"
        assert machine["status_color"] != red

    def test_with_no_push_the_record_stands(self, client, gw):
        seed_machine(gw, status="RED", reason="Flash Point out of spec")

        machine = machines_of(client)["pac-flash-2"]

        assert machine["status"] == "RED"
        assert machine["reason"] == "Flash Point out of spec"
        assert machine["live"] is False

    def test_an_expired_push_falls_back_to_the_record(self, app, client, gw):
        seed_machine(gw, status="RED")
        push(client, status="GREEN")
        # Age the entry past its TTL rather than sleeping through it.
        entry = app.config["LIVE"]._entries["pac-flash-2"]
        entry["seen"] -= (entry["ttl"] + 1)

        machine = machines_of(client)["pac-flash-2"]

        assert machine["status"] == "RED"
        assert machine["live"] is False

    def test_a_bench_that_pushes_reads_as_running(self, client, gw):
        """A module talking to us IS the liveness signal — better than
        inferring it from the age of a five-minute heartbeat write."""
        seed_machine(gw)
        push(client)

        assert machines_of(client)["pac-flash-2"]["state"] == "running"

    def test_the_record_keeps_everything_the_push_does_not_carry(self, client, gw):
        seed_machine(gw, title="PAC Flash 2")
        push(client, status="GREEN")

        machine = machines_of(client)["pac-flash-2"]

        assert machine["title"] == "PAC Flash 2"
        assert "qc_specs" in machine and "sub_statuses" in machine

    def test_a_machine_nobody_pushed_for_is_untouched(self, client, gw):
        seed_machine(gw, uid="m-other", title="Multitek NS", status="GREEN")
        push(client)   # a different machine

        machine = machines_of(client)["m-other"]

        assert machine["status"] == "GREEN"
        assert machine["live"] is False

    def test_the_age_shown_is_the_benchs_own_timestamp(self, client, gw):
        seed_machine(gw, at="2026-08-05T13:00:00")
        push(client, at="2026-08-05T14:02:11")

        machine = machines_of(client)["pac-flash-2"]

        assert machine["updated_at"] == "2026-08-05T14:02:11"


def events_of(client, limit=50):
    return client.get(f"/api/events?limit={limit}").get_json()["events"]


class TestALiveParseBlipsAtOnce:
    """A run should light the floor when the bench says so, not when its log
    row has cleared the write queue and been picked up by the snapshot."""

    def test_a_parse_appears_without_waiting_for_labcore(self, client, gw):
        seed_machine(gw)
        push(client, last_parse_at="2026-08-05T14:02:10", lab_id="L-1234")

        blips = events_of(client)

        assert blips[0]["machine_uid"] == "pac-flash-2"
        assert blips[0]["lab_id"] == "L-1234"
        assert blips[0]["kind"] == "run"

    def test_a_push_with_no_parse_blips_nothing(self, client, gw):
        seed_machine(gw)
        push(client)

        assert events_of(client) == []

    def test_the_same_run_arriving_later_does_not_blip_twice(self, client, gw):
        seed_machine(gw)
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log ("
               "machine_uid TEXT, ts TEXT, kind TEXT, lab_id TEXT, "
               "test_name TEXT, value TEXT, detail TEXT)")
        gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
               ["pac-flash-2", "2026-08-05T14:02:10", "run", "L-1234",
                "", "", "{}"])
        push(client, last_parse_at="2026-08-05T14:02:10", lab_id="L-1234")
        client.get("/api/machines?fresh=1")      # let the snapshot catch up

        blips = [e for e in events_of(client) if e["lab_id"] == "L-1234"]

        assert len(blips) == 1

    def test_recorded_events_still_come_through(self, client, gw):
        seed_machine(gw)
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log ("
               "machine_uid TEXT, ts TEXT, kind TEXT, lab_id TEXT, "
               "test_name TEXT, value TEXT, detail TEXT)")
        gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
               ["pac-flash-2", "2026-08-05T13:00:00", "run", "L-0001",
                "", "", "{}"])
        client.get("/api/machines?fresh=1")

        assert any(e["lab_id"] == "L-0001" for e in events_of(client))


class TestTheFactoryStaysSideEffectFree:
    """`create_app` must not talk to LabCore. An app factory with side effects
    is what gave every test its own poller before the snapshot was moved to
    boot; publishing the live config has exactly the same shape."""

    def test_creating_the_app_publishes_nothing(self, gw):
        from web_app import create_app
        create_app(gw, authenticator=StubAuth(), secret="s")

        rows = gw.read_sql("SELECT key, value FROM lem_meta").get("rows")
        assert not rows

    def test_boot_publishes_the_address_and_token(self, gw, app):
        from live_presence import LIVE_TOKEN_KEY, LIVE_URL_KEY, start_live_channel

        url = start_live_channel(app, gw, "10.0.0.5", 5557)

        rows = gw.read_sql("SELECT key, value FROM lem_meta").get("rows") or []
        published = {r["key"]: r["value"] for r in rows}
        assert url == "http://10.0.0.5:5557"
        assert published[LIVE_URL_KEY] == "http://10.0.0.5:5557"
        assert published[LIVE_TOKEN_KEY] == "test-token"

    def test_boot_survives_a_labcore_that_refuses(self, app):
        from live_presence import start_live_channel

        class Dead:
            def sql(self, *a, **k):
                raise RuntimeError("queue full")

        assert start_live_channel(app, Dead(), "10.0.0.5", 5557)


class TestTheAddressBenchesAreGiven:
    def test_an_explicit_address_wins(self):
        from live_presence import live_url
        assert live_url("0.0.0.0", 5557,
                        env_url="http://lem.lab:8080/") == "http://lem.lab:8080"

    def test_a_wildcard_bind_is_resolved_to_something_reachable(self):
        """Benches cannot POST to 0.0.0.0 — publishing that would hand every
        module an address that can never work."""
        from live_presence import live_url
        assert live_url("0.0.0.0", 5557,
                        resolver=lambda: "10.0.0.5") == "http://10.0.0.5:5557"

    def test_a_named_host_is_used_as_given(self):
        from live_presence import live_url
        assert live_url("10.0.0.9", 5557) == "http://10.0.0.9:5557"


def station_module():
    """The station module, loaded the way LabStation loads it — a lone file.

    Same trick as test_qc_window.py: the two programs cannot import each other,
    so the only way to prove they still agree about the wire format is to run
    both halves in one test.
    """
    import importlib.util
    import pathlib
    path = (pathlib.Path(__file__).resolve().parent.parent.parent
            / "LEM Station Module" / "lem_station_module.py")
    if not path.exists():
        pytest.skip("station module not present next to the web server")
    spec = importlib.util.spec_from_file_location("_lem_mod_for_live_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTheTwoProgramsStillAgree:
    """The module builds the payload; this server has to understand it. Nothing
    else in either suite would notice if one side renamed a field."""

    def test_the_payload_the_bench_builds_is_accepted(self, client, gw):
        station = station_module()
        seed_machine(gw, status="RED", reason="Flash Point out of spec")
        machine = station.Machine(uid="pac-flash-2", title="PAC Flash 2")
        evaluation = station.MachineEvaluation(status="GREEN", reason="")
        rows = [{station.LAB_ID_KEY: "L-1234",
                 "parsed_date": "2026-08-05", "parsed_time": "14:02:10"}]

        body = station.build_live_payload(
            machine, evaluation, __import__("datetime").datetime(
                2026, 8, 5, 14, 2, 11), 30, rows)
        response = client.post("/api/live", json=body,
                               headers={"X-LEM-Token": "test-token"})

        assert response.status_code == 204
        machines = machines_of(client)
        assert machines["pac-flash-2"]["status"] == "GREEN"
        assert machines["pac-flash-2"]["live"] is True
        assert any(e["lab_id"] == "L-1234" for e in events_of(client))

    def test_both_sides_name_the_config_keys_the_same(self):
        import live_presence
        station = station_module()
        assert station.LIVE_URL_KEY == live_presence.LIVE_URL_KEY
        assert station.LIVE_TOKEN_KEY == live_presence.LIVE_TOKEN_KEY

    def test_both_sides_agree_on_the_endpoint_path(self):
        station = station_module()
        assert station.LIVE_PATH == "/api/live"

    def test_the_ttl_the_server_picks_covers_the_benchs_interval(self):
        """The module's slowest offered interval must not outlive its TTL, or
        that bench flaps between live and from-record forever."""
        from live_presence import ttl_for
        for interval in (15, 30, 60, 300):
            assert ttl_for(interval) > interval, interval
