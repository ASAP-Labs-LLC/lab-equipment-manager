"""The bench telling the floor about itself, directly.

Everything the lab records still goes to LabCore. This is the second road, and
it carries only what the module alone can know: I am running, my status is now
X, I just parsed L-1234. The floor used to infer all three from the age of a
`lem_machine_heartbeat` row written every five minutes through the same queue
as everything else.

The rules this road lives under, both from CLAUDE.md:
  * no pip dependencies — LabStation bundles PySide6 and little else, so the
    POST is stdlib `urllib`;
  * a worker must never raise — LabStation's `_run_in_thread` drops the
    callback on an exception, which strands `_polling` and stops the bench
    polling at all. A push that fails must be a no-op, not an incident.
"""
import json
from datetime import datetime

import pytest

import lem_station_module as mod
from lem_station_module import LAB_ID_KEY, Machine, MethodMapping, Selector, TestSpec

NOW = datetime(2026, 8, 5, 14, 2, 11)


def bench():
    return Machine(
        uid="pac-flash-2", title="PAC Flash 2", source_type="single_csv",
        delimiter=",", lab_id=Selector(mode="cell", index=0),
        mappings=[MethodMapping(methods=["Flash Point"],
                                selector=Selector(mode="cell", index=1))],
        tests=[TestSpec(name="Flash Point", value_col="Flash Point",
                        expected=62.5, std_dev=1.0, k=2.0, sample_id="QC1")])


class TestReadingWhereToPush:
    """The address and token live in `lem_meta`, which the module already has a
    LabCore connection for — so a bench that moves to another PC picks them up
    with nothing typed on it."""

    def test_both_keys_are_read(self):
        sql, args = mod.build_live_config_query()
        assert "lem_meta" in sql
        assert "live_url" in args and "live_token" in args

    def test_rows_become_an_address_and_a_token(self):
        url, token = mod.parse_live_config([
            {"key": "live_url", "value": "http://10.0.0.5:5557"},
            {"key": "live_token", "value": "tok"}])
        assert (url, token) == ("http://10.0.0.5:5557", "tok")

    def test_a_trailing_slash_is_not_kept(self):
        url, _ = mod.parse_live_config(
            [{"key": "live_url", "value": "http://10.0.0.5:5557/"}])
        assert url == "http://10.0.0.5:5557"

    def test_nothing_published_yet_reads_as_no_channel(self):
        assert mod.parse_live_config([]) == ("", "")

    def test_junk_rows_do_not_crash_the_poll(self):
        assert mod.parse_live_config([{"nope": 1}, None]) == ("", "")


class TestWhatTheBenchSays:
    def test_it_carries_the_status_just_evaluated(self):
        evaluation = mod.MachineEvaluation(status=mod.STATUS_GREEN, reason="")
        payload = mod.build_live_payload(bench(), evaluation, NOW, 30, [])
        assert payload["machine_uid"] == "pac-flash-2"
        assert payload["status"] == "GREEN"
        assert payload["at"] == "2026-08-05T14:02:11"

    def test_it_carries_the_reason_for_a_bad_status(self):
        evaluation = mod.MachineEvaluation(
            status=mod.STATUS_RED, reason="Flash Point out of spec")
        payload = mod.build_live_payload(bench(), evaluation, NOW, 30, [])
        assert payload["reason"] == "Flash Point out of spec"

    def test_it_states_its_own_poll_interval(self):
        """The server sizes this machine's TTL from it — without it a bench on
        the 5-minute interval would flap between live and from-record."""
        payload = mod.build_live_payload(
            bench(), mod.MachineEvaluation(status="GREEN", reason=""),
            NOW, 300, [])
        assert payload["interval_seconds"] == 300

    def test_it_reports_what_it_just_parsed(self):
        rows = [{LAB_ID_KEY: "L-1234", "Flash Point": 62.5,
                 "parsed_date": "2026-08-05", "parsed_time": "14:02:10"}]
        payload = mod.build_live_payload(
            bench(), mod.MachineEvaluation(status="GREEN", reason=""),
            NOW, 30, rows)
        assert payload["lab_id"] == "L-1234"
        assert payload["last_parse_at"] == "2026-08-05T14:02:10"

    def test_the_newest_row_of_a_burst_is_the_one_reported(self):
        rows = [{LAB_ID_KEY: "L-1", "parsed_date": "2026-08-05",
                 "parsed_time": "14:02:01"},
                {LAB_ID_KEY: "L-2", "parsed_date": "2026-08-05",
                 "parsed_time": "14:02:09"}]
        payload = mod.build_live_payload(
            bench(), mod.MachineEvaluation(status="GREEN", reason=""),
            NOW, 30, rows)
        assert payload["lab_id"] == "L-2"

    def test_a_poll_that_parsed_nothing_claims_no_parse(self):
        payload = mod.build_live_payload(
            bench(), mod.MachineEvaluation(status="GREEN", reason=""),
            NOW, 30, [])
        assert "lab_id" not in payload
        assert "last_parse_at" not in payload

    def test_a_row_with_no_lab_id_is_not_a_parse_to_announce(self):
        rows = [{"Flash Point": 62.5, "parsed_date": "2026-08-05",
                 "parsed_time": "14:02:10"}]
        payload = mod.build_live_payload(
            bench(), mod.MachineEvaluation(status="GREEN", reason=""),
            NOW, 30, rows)
        assert "lab_id" not in payload

    def test_the_bookkeeping_never_travels_this_road_either(self):
        """__raw__ and __corrections__ are the row's record, not something the
        floor needs to draw a blip."""
        rows = mod.apply_row_corrections(
            [{LAB_ID_KEY: "L-1234", "Flash Point": 65.5}], {"Flash Point": -3.0})
        payload = mod.build_live_payload(
            bench(), mod.MachineEvaluation(status="GREEN", reason=""),
            NOW, 30, rows)
        assert mod.RAW_KEY not in str(payload)
        assert mod.CORRECTION_KEY not in str(payload)


class Recorder:
    """Stands in for urlopen, capturing what would have gone over the wire."""

    def __init__(self, status=204, boom=None):
        self.status = status
        self.boom = boom
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self.boom is not None:
            raise self.boom
        return self

    # context-manager shape of an http response
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def urlopen(monkeypatch):
    def install(recorder):
        monkeypatch.setattr(mod.urllib.request, "urlopen", recorder)
        return recorder
    return install


class TestPostingToTheFloor:
    def test_it_posts_to_the_live_endpoint(self, urlopen):
        rec = urlopen(Recorder())

        assert mod.post_live("http://10.0.0.5:5557", "tok",
                             {"machine_uid": "m1"}) is True

        request, timeout = rec.requests[0]
        assert request.full_url == "http://10.0.0.5:5557/api/live"
        assert request.get_method() == "POST"
        assert timeout == mod.LIVE_TIMEOUT

    def test_it_carries_the_token_and_the_payload(self, urlopen):
        rec = urlopen(Recorder())

        mod.post_live("http://10.0.0.5:5557", "tok", {"machine_uid": "m1"})

        request, _ = rec.requests[0]
        assert request.get_header("X-lem-token") == "tok"
        assert json.loads(request.data.decode("utf-8")) == {"machine_uid": "m1"}

    def test_a_server_that_refuses_is_a_no_op_not_an_incident(self, urlopen):
        """A worker that raises strands _polling — the bench stops polling at
        all. Losing a status update is a lesser problem by a wide margin."""
        import urllib.error
        urlopen(Recorder(boom=urllib.error.URLError("connection refused")))

        assert mod.post_live("http://10.0.0.5:5557", "tok", {}) is False

    def test_a_timeout_is_a_no_op(self, urlopen):
        urlopen(Recorder(boom=TimeoutError("timed out")))
        assert mod.post_live("http://10.0.0.5:5557", "tok", {}) is False

    def test_a_rejected_token_is_a_no_op(self, urlopen):
        import urllib.error
        urlopen(Recorder(boom=urllib.error.HTTPError(
            "http://x", 401, "Not authorised", {}, None)))
        assert mod.post_live("http://10.0.0.5:5557", "tok", {}) is False

    def test_anything_at_all_going_wrong_is_a_no_op(self, urlopen):
        urlopen(Recorder(boom=ValueError("nonsense url")))
        assert mod.post_live("http://10.0.0.5:5557", "tok", {}) is False

    def test_no_address_means_no_attempt(self, urlopen):
        rec = urlopen(Recorder())
        assert mod.post_live("", "tok", {}) is False
        assert rec.requests == []

    def test_no_pip_dependency_is_used(self):
        """LabStation bundles PySide6 and little else."""
        import inspect
        source = inspect.getsource(mod.post_live)
        assert "requests" not in source
        assert "urllib" in source


from test_module_qt import make_module        # noqa: E402


@pytest.fixture
def polling(monkeypatch, qapp):
    """A module wired to a fake LabCore and a fake floor, so a whole poll can
    be run and what it pushed inspected."""
    module = make_module()
    posted, reads = [], []

    monkeypatch.setitem(mod.__dict__, "labcore_write",
                        lambda op, params=None, **kw: {"ok": True})
    monkeypatch.setitem(mod.__dict__, "labcore_sql",
                        lambda sql, args=None, **kw: {"ok": True})

    def read_sql(sql, args=None, **kw):
        reads.append(sql)
        if "lem_meta" in sql:
            return {"rows": [{"key": "live_url", "value": "http://10.0.0.5:5557"},
                             {"key": "live_token", "value": "tok"}]}
        return {"error": "offline"}

    monkeypatch.setitem(mod.__dict__, "labcore_read_sql", read_sql)

    def fake_post(url, token, payload, timeout=None):
        posted.append((url, token, payload))
        return True

    monkeypatch.setattr(mod, "post_live", fake_post)
    return module, posted, reads


def poll(module, machine=None, prints=("QC1,65.5",)):
    return module._process_outcome(machine or bench(), list(prints), None,
                                   [], NOW)


class TestThePollAnnouncesItself:
    def test_a_completed_poll_pushes_once(self, polling):
        module, posted, _ = polling
        poll(module)
        assert len(posted) == 1

    def test_it_pushes_the_status_it_just_evaluated(self, polling):
        module, posted, _ = polling
        payload = poll(module)
        _, _, body = posted[0]
        assert body["status"] == payload["evaluation"].status
        assert body["machine_uid"] == "pac-flash-2"

    def test_it_pushes_the_lab_id_it_just_parsed(self, polling):
        module, posted, _ = polling
        poll(module)
        assert posted[0][2]["lab_id"] == "QC1"

    def test_it_states_the_interval_it_is_polling_at(self, polling):
        module, posted, _ = polling
        module._poll_seconds = 300
        poll(module)
        assert posted[0][2]["interval_seconds"] == 300

    def test_a_poll_that_could_not_ingest_still_says_it_is_alive(self, polling):
        """A bench with an unreadable folder is still a running module — that
        is exactly when the floor most needs to know it is there."""
        module, posted, _ = polling
        module._process_outcome(bench(), [], "CSV not found", [], NOW)
        assert len(posted) == 1
        assert posted[0][2]["status"] == mod.STATUS_UNKNOWN


class TestWhenThereIsNoFloorToTalkTo:
    def test_nothing_published_means_no_push_attempted(self, monkeypatch, qapp):
        module = make_module()
        attempts = []
        monkeypatch.setitem(mod.__dict__, "labcore_read_sql",
                            lambda sql, args=None, **kw: {"rows": []})
        monkeypatch.setitem(mod.__dict__, "labcore_sql",
                            lambda sql, args=None, **kw: {"ok": True})
        monkeypatch.setitem(mod.__dict__, "labcore_write",
                            lambda op, params=None, **kw: {"ok": True})
        monkeypatch.setattr(mod, "post_live",
                            lambda *a, **k: attempts.append(a) or True)

        poll(module)

        assert attempts == []

    def test_a_dead_floor_leaves_the_poll_untouched(self, monkeypatch, qapp):
        """The push is an accelerator. Nothing about the poll depends on it."""
        module = make_module()
        monkeypatch.setitem(mod.__dict__, "labcore_read_sql",
                            lambda sql, args=None, **kw: (
                                {"rows": [{"key": "live_url",
                                           "value": "http://10.0.0.5:5557"}]}
                                if "lem_meta" in sql else {"error": "offline"}))
        monkeypatch.setitem(mod.__dict__, "labcore_sql",
                            lambda sql, args=None, **kw: {"ok": True})
        monkeypatch.setitem(mod.__dict__, "labcore_write",
                            lambda op, params=None, **kw: {"ok": True})

        def refuse(*a, **k):
            raise OSError("connection refused")

        monkeypatch.setattr(mod, "post_live", refuse)

        payload = poll(module)      # must not raise

        assert payload["rows"][0][LAB_ID_KEY] == "QC1"
        assert payload["evaluation"] is not None


class TestTheAddressIsReadSparingly:
    def test_the_config_is_read_once_not_every_poll(self, polling):
        module, _, reads = polling
        for _ in range(5):
            poll(module)
        assert sum("lem_meta" in sql for sql in reads) == 1

    def test_repeated_failures_make_it_look_again(self, polling, monkeypatch):
        """So a moved server or a rotated token heals itself instead of
        needing a restart on every bench."""
        module, _, reads = polling
        monkeypatch.setattr(mod, "post_live", lambda *a, **k: False)

        for _ in range(mod.LIVE_RETRY_AFTER + 1):
            poll(module)

        assert sum("lem_meta" in sql for sql in reads) >= 2


class TestItHappensOnTheWorker:
    def test_the_push_is_issued_from_the_worker_half(self):
        """All LabCore and network traffic belongs in the worker; the main
        thread half only touches widgets."""
        import inspect
        worker = inspect.getsource(mod.LEMStationModule._process_outcome)
        main = inspect.getsource(mod.LEMStationModule._show_outcome)
        assert "_push_live" in worker or "_pushed" in worker
        assert "_push_live" not in main
