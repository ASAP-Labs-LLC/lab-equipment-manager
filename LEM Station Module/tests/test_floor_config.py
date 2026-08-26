"""The bench reads its configuration from the FLOOR, and LabCore only when the
floor cannot answer.

LabCore serialises reads AND writes through one queue at about 1.5 ops/sec. Its
database lives on an SMB share and cannot be moved to local disk, so `read_sql`
will always consume a write-queue slot — every question a bench asks delays
every other bench's results. That is settled and is not what this changes.

What this changes is WHO is asked. Each bench currently reads its own
configuration out of LabCore, so LabCore's load grows with the number of
benches, which is what is crashing it. The floor's web server sits next to
LabCore and already holds every one of those tables in an in-memory snapshot it
refreshes every 12 seconds at a cost that does not grow with the bench count.
Asking it instead turns N benches x 5 reads per window into one refresh:

    GET {live_url}/api/bench/{machine_uid}/config
      Header: X-LEM-Token: {live_token}
      200 {"machine_uid", "snapshot_age_seconds", "override",
           "corrections", "qc_samples", "qc_targets", "qc_specs",
           "maintenance"}
      401 bad token
      503 {"error": ..., "stale": true}   snapshot never populated

The row shapes are EXACTLY what `read_sql` hands back for those tables, so they
feed the existing parsers unchanged. That is deliberate, and the equivalence
test in section (F) is the proof: the same rows through either road must leave
the bench in the same state, or the floor road is a second, divergent
implementation of the bench's configuration and nobody would ever find the
disagreement.

**The fallback is the whole safety story.** The floor is another program on
another schedule on another PC: it may be un-upgraded, rebooting, behind a proxy
serving a login page, or holding a snapshot that stopped refreshing hours ago.
Every one of those has to end with the bench reading LabCore, because the
alternative is a bench running on configuration nobody can vouch for. Each
branch is tested here by name.

The age bound is not a nicety. Correction factors are added to EVERY measurement
before it is written, displayed or QC-judged (ISO/IEC 17025 section 7.8.2).
Serving them from the floor is acceptable because the floor's snapshot is
strictly FRESHER than the window it replaces — 12s against a 900s backstop — and
LabCore remains the origin. That argument holds only while the age is bounded:
an unbounded stale snapshot means applying a calibration offset the lab has
already superseded, which is exactly the finding this module exists to avoid.
"""
import json
from datetime import datetime, timedelta

import pytest

import lem_station_module as mod
from lem_station_module import LAB_ID_KEY, Machine

from test_module_qt import make_module

NOW = datetime(2026, 8, 26, 12, 0, 0)
FLOOR = "http://10.0.0.5:5557"
UID = "m1"


# ── The rows the lab actually has, in LabCore's own shapes ──────────────────
#
# One set, used by BOTH roads. Section (F) depends on that: two hand-written
# expectations can agree with each other and disagree with the module.

CORRECTION_ROWS = [{"test_name": "Flash", "correction": -3.0},
                   {"test_name": "Density", "correction": 0.0012}]

QC_SAMPLE_ROWS = [{"name": "CRM-A", "sample_id_val": "STD-100",
                   "tests": json.dumps([
                       {"name": "Flash", "value_col": "Flash",
                        "expected": 66.0, "std_dev": 0.5, "k": 2.0,
                        "units": "C"}])}]

QC_TARGET_ROWS = [{"sample_name": "CRM-A", "test_name": "Flash"}]

QC_SPEC_ROWS = [{"machine_uid": UID, "test_name": "Density",
                 "sample_id": "STD-100", "expected": 0.8500,
                 "std_dev": 0.0005, "k": 2.0, "units": "g/mL"}]

MAINT_ROWS = [{"uid": "pm-1", "name": "Annual calibration",
               "kind": "calibration", "interval_days": 365,
               "last_done": "2026-01-04", "note": "vendor"}]

OVERRIDE = mod.STATUS_SERVICE


def floor_body(uid=UID, age=4.2, **overrides):
    """A well-formed answer from the floor, on the fixed wire contract."""
    body = {"machine_uid": uid,
            "snapshot_age_seconds": age,
            "override": OVERRIDE,
            "corrections": list(CORRECTION_ROWS),
            "qc_samples": list(QC_SAMPLE_ROWS),
            "qc_targets": list(QC_TARGET_ROWS),
            "qc_specs": list(QC_SPEC_ROWS),
            "maintenance": list(MAINT_ROWS)}
    body.update(overrides)
    return body


class LabCore:
    """A LabCore that counts what it is asked for, and answers with the SAME
    rows the floor serves — so a read that fell back is a read that shows up
    in `reads`, never a difference in what the bench ends up holding.

    The signatures are LabStation's REAL ones: `read_sql` takes NO `source`.
    A fake looser than the thing it stands in for is how a call that raises
    TypeError in production sails through its test.
    """

    def __init__(self, live_url=FLOOR):
        self.reads = []
        self.live_url = live_url
        self.refuse = set()

    def _answer(self, tag, rows):
        self.reads.append(tag)
        if tag in self.refuse:
            return {"error": "LabCore is busy"}
        return {"rows": rows}

    def read_sql(self, sql, args=None, timeout=None):
        flat = " ".join(sql.split()).lower()
        if "lem_meta" in flat:
            self.reads.append("live_config")
            if not self.live_url:
                return {"rows": []}
            return {"rows": [{"key": "live_url", "value": self.live_url},
                             {"key": "live_token", "value": "tok"}]}
        if "lem_correction_factors" in flat:
            return self._answer("corrections", list(CORRECTION_ROWS))
        if "lem_qc_samples" in flat:
            return self._answer("qc_samples", list(QC_SAMPLE_ROWS))
        if "lem_machine_targets" in flat:
            return self._answer("targets", list(QC_TARGET_ROWS))
        if "lem_qc_specs" in flat:
            return self._answer("qc_specs", list(QC_SPEC_ROWS))
        if "lem_maintenance" in flat or "interval_days" in flat:
            return self._answer("maint", list(MAINT_ROWS))
        if "lem_machine_control" in flat:
            return self._answer("override", [{"machine_uid": UID,
                                              "manual_override": OVERRIDE}])
        self.reads.append("other")
        return {"rows": []}

    def sql(self, sql, args=None, source="LabStation", timeout=None):
        return {"ok": True}

    def write(self, operation, params=None, source=""):
        return {"ok": True}

    def count(self, tag):
        return self.reads.count(tag)

    def config_reads(self):
        """Everything this task is meant to stop asking LabCore for."""
        return [r for r in self.reads
                if r in ("corrections", "qc_samples", "targets", "qc_specs",
                         "maint", "override")]


# `None` is a MEANINGFUL body — it is what `fetch_floor_config` returns for a
# floor that did not answer — so it cannot double as "use the default".
_DEFAULT_BODY = object()


class FloorServer:
    """The floor on the other end of the config GET.

    `body` is what it answers with; a callable is handed the request number so
    a floor can go dark — or come back — mid-shift. `body=None` is a floor that
    gave no answer at all. `boom` raises instead.
    """

    def __init__(self, body=_DEFAULT_BODY, boom=None, during=None):
        self.body = floor_body() if body is _DEFAULT_BODY else body
        self.boom = boom
        self.during = during      # the GUI thread acting mid-read
        self.calls = []

    def fetch(self, url, token, machine_uid, timeout=None):
        self.calls.append((url, token, machine_uid, timeout))
        if self.during is not None:
            act, self.during = self.during, None
            act()
        if self.boom is not None:
            raise self.boom
        body = self.body
        return body(len(self.calls)) if callable(body) else body


@pytest.fixture
def bench(qapp, monkeypatch):
    """A bench whose note channel is already proven healthy.

    `_live_delivering` is what a floor answering `/api/live` in the note
    protocol sets, and `_live_channel_healthy()` is the gate this whole road
    hangs off. Set here rather than re-derived, so a test about the CONFIG road
    is not also a test of the note road — section (E) drives it for real.
    """

    def build(labcore, floor=None, machine=None, healthy=True):
        monkeypatch.setitem(mod.__dict__, "labcore_read_sql", labcore.read_sql)
        monkeypatch.setitem(mod.__dict__, "labcore_sql", labcore.sql)
        monkeypatch.setitem(mod.__dict__, "labcore_write", labcore.write)
        monkeypatch.setattr(mod, "_in_thread", lambda fn, cb: cb(fn()))
        monkeypatch.setattr(mod, "post_live", lambda *a, **kw: {"stale": []})
        if floor is not None:
            monkeypatch.setattr(mod, "fetch_floor_config", floor.fetch)
        module = make_module()
        module._machine = machine or Machine(uid=UID, title="Eraspec",
                                             source_type="manual")
        if healthy:
            module._live_url = labcore.live_url
            module._live_token = "tok"
            module._live_delivering = True
            module._live_failures = 0
        return module
    return build


def poll(module, now):
    """One whole worker-half poll, with the clock injected."""
    return module._process_outcome(module._machine, [], None, [], now)


def configured(machine):
    """Everything a config read is supposed to have put on the bench."""
    return {"corrections": dict(machine.corrections or {}),
            "tests": [s.to_dict() for s in machine.tests],
            "maintenance": [t.to_dict() for t in machine.maintenance],
            "override": machine.manual_override}


# ── (A) The GET itself: stdlib, short, and it never raises ──────────────────
#
# The same construction as `post_live`, for the same reason. This runs on the
# worker, and LabStation's `_run_in_thread` DROPS the callback when the worker
# raises — which strands `_polling` and the bench stops polling altogether.
# Losing a configuration read is a small problem; losing the poll is the bench.


class Response:
    """Stands in for urlopen's context manager."""

    def __init__(self, body=None, status=200, boom=None):
        self.body = body
        self.status = status
        self.boom = boom
        self.requests = []
        self.timeouts = []
        if body is not None:
            self.read = lambda: (body.encode("utf-8")
                                 if isinstance(body, str) else body)

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.boom is not None:
            raise self.boom
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def wire(monkeypatch):
    def install(response):
        monkeypatch.setattr(mod.urllib.request, "urlopen", response)
        return response
    return install


class TestTheConfigGet:
    def test_it_asks_the_agreed_url_with_the_agreed_token(self, wire):
        """The contract is fixed — the server is being built against exactly
        this, and a bench that asks a different URL simply never gets an
        answer and quietly costs LabCore the reads for ever."""
        rec = wire(Response(json.dumps(floor_body())))
        mod.fetch_floor_config(FLOOR, "tok", UID)
        request = rec.requests[0]
        assert request.full_url == f"{FLOOR}/api/bench/{UID}/config"
        assert request.get_method() == "GET"
        assert request.get_header("X-lem-token") == "tok"

    def test_a_uid_with_awkward_characters_is_escaped(self, wire):
        """The machine uid is operator-typed and lands in a URL PATH. A space
        or a slash in it must not build a request for some other bench's
        configuration, or a bench applies another instrument's calibration
        offsets to its own measurements."""
        rec = wire(Response(json.dumps(floor_body(uid="pac flash/2"))))
        mod.fetch_floor_config(FLOOR, "tok", "pac flash/2")
        assert rec.requests[0].full_url == (
            f"{FLOOR}/api/bench/pac%20flash%2F2/config")

    def test_a_good_answer_comes_back_as_a_dict(self, wire):
        wire(Response(json.dumps(floor_body())))
        assert mod.fetch_floor_config(FLOOR, "tok", UID) == floor_body()

    def test_no_address_means_no_attempt(self, wire):
        rec = wire(Response(json.dumps(floor_body())))
        assert mod.fetch_floor_config("", "tok", UID) is None
        assert rec.requests == []

    def test_a_rejected_token_is_no_answer(self, wire):
        """401. urlopen RAISES HTTPError for this, which is the shape the real
        floor produces; it must arrive as None, not as an exception on the
        worker."""
        wire(Response(boom=mod.urllib.error.HTTPError(
            FLOOR, 401, "Unauthorized", {}, None)))
        assert mod.fetch_floor_config(FLOOR, "bad", UID) is None

    def test_a_snapshot_that_never_populated_is_no_answer(self, wire):
        """503 — the floor is up but has nothing to serve yet, which is every
        floor for the first few seconds after a restart."""
        wire(Response(boom=mod.urllib.error.HTTPError(
            FLOOR, 503, "Service Unavailable", {},
            None)))
        assert mod.fetch_floor_config(FLOOR, "tok", UID) is None

    def test_a_non_2xx_status_without_a_raise_is_no_answer(self, wire):
        """Belt to the braces above: a gateway or a stubbed opener can hand
        back a 503 as an ordinary response object rather than raising."""
        wire(Response(json.dumps({"error": "cold", "stale": True}), status=503))
        assert mod.fetch_floor_config(FLOOR, "tok", UID) is None

    def test_a_proxy_serving_html_is_no_answer_and_never_raises(self, wire):
        wire(Response("<!doctype html><title>Sign in</title>"))
        assert mod.fetch_floor_config(FLOOR, "tok", UID) is None

    def test_a_json_array_is_no_answer(self, wire):
        """Valid JSON, wrong shape. `.get` on a list is an AttributeError on
        the worker."""
        wire(Response(json.dumps([1, 2, 3])))
        assert mod.fetch_floor_config(FLOOR, "tok", UID) is None

    def test_an_empty_body_is_no_answer(self, wire):
        """Unlike `post_live`, where an empty body means a push that LANDED.
        Here there is nothing to configure from, so it is not an answer."""
        wire(Response())
        assert mod.fetch_floor_config(FLOOR, "tok", UID) is None

    def test_a_timeout_is_no_answer(self, wire):
        wire(Response(boom=TimeoutError("timed out")))
        assert mod.fetch_floor_config(FLOOR, "tok", UID) is None

    def test_anything_at_all_going_wrong_is_no_answer(self, wire):
        """Total, like `post_live`. Whatever a proxy, a DNS failure or a
        half-written stdlib does, the worker must come out the other side."""
        for boom in (mod.urllib.error.URLError("refused"),
                     ValueError("nonsense url"),
                     RuntimeError("something else entirely"),
                     KeyError("surprise")):
            wire(Response(boom=boom))
            assert mod.fetch_floor_config(FLOOR, "tok", UID) is None

    def test_it_uses_a_short_timeout(self, wire):
        """In the spirit of LIVE_TIMEOUT. This is on the poll's critical path
        and the floor is one hop away on the LAN — a long timeout would freeze
        the poll behind a server that is merely rebooting."""
        rec = wire(Response(json.dumps(floor_body())))
        mod.fetch_floor_config(FLOOR, "tok", UID)
        assert rec.timeouts[0] == mod.FLOOR_CONFIG_TIMEOUT
        assert 0 < mod.FLOOR_CONFIG_TIMEOUT <= 3.0


# ── (B) What counts as an answer worth using ────────────────────────────────


class TestOnlyAWholeFreshAnswerIsUsable:
    def test_a_good_body_becomes_labcore_shaped_results(self):
        """The rows are handed on in the exact shape `read_sql` returns them,
        which is what lets the existing parsers stay untouched."""
        results = mod.floor_config_results(floor_body(), UID)
        assert results["qc_samples"] == {"rows": QC_SAMPLE_ROWS}
        assert results["targets"] == {"rows": QC_TARGET_ROWS}
        assert results["qc_specs"] == {"rows": QC_SPEC_ROWS}
        assert results["maint"] == {"rows": MAINT_ROWS}
        assert results["corrections"] == {"rows": CORRECTION_ROWS}
        assert results["override"] == {
            "rows": [{"machine_uid": UID, "manual_override": OVERRIDE}]}

    def test_nothing_it_returns_carries_an_error(self):
        """`_labcore_sync` decides "did this source answer?" by looking for an
        `error` key. A usable floor result is an answer by construction."""
        results = mod.floor_config_results(floor_body(), UID)
        assert all(not r.get("error") for r in results.values())

    def test_a_stale_snapshot_is_refused(self):
        """THE compliance gate. Past the bound the floor's rows may name a
        correction factor the lab has already replaced, and that offset is
        added to every measurement before it is written, shown or judged
        (ISO/IEC 17025 section 7.8.2). Refused means LabCore is asked, which
        costs a queue slot and is always the right trade."""
        old = mod.FLOOR_CONFIG_MAX_AGE_SECONDS + 0.1
        assert mod.floor_config_results(floor_body(age=old), UID) is None

    def test_the_bound_tolerates_a_few_missed_refreshes(self):
        """It refreshes every 12s. A bound so tight that one slow cycle
        stampedes every bench back onto LabCore would defeat the point."""
        assert mod.FLOOR_CONFIG_MAX_AGE_SECONDS >= 36
        assert mod.FLOOR_CONFIG_MAX_AGE_SECONDS <= mod.CONFIG_REFRESH_SECONDS

    def test_a_snapshot_inside_the_bound_is_used(self):
        inside = mod.FLOOR_CONFIG_MAX_AGE_SECONDS - 0.1
        assert mod.floor_config_results(floor_body(age=inside), UID) is not None

    def test_a_missing_age_is_refused(self):
        """No age is not "fresh", it is a floor that cannot tell us — an older
        server, or one whose refresher died. The gate must be impossible to
        pass by omitting the field."""
        body = floor_body()
        del body["snapshot_age_seconds"]
        assert mod.floor_config_results(body, UID) is None

    def test_a_non_numeric_age_is_refused(self):
        for age in ("4.2 seconds", None, True, [4.2], {}):
            assert mod.floor_config_results(floor_body(age=age), UID) is None, \
                f"{age!r} was accepted as a snapshot age"

    def test_a_negative_age_is_refused(self):
        """A snapshot from the future is two clocks disagreeing, not a fresh
        one. The arithmetic has stopped meaning anything, and the one direction
        it is safe to be wrong in costs a LabCore read."""
        assert mod.floor_config_results(floor_body(age=-1.0), UID) is None

    def test_an_answer_about_another_bench_is_refused(self):
        """A caching proxy, or a floor that resolved the uid differently. Its
        correction factors are another instrument's, and applying them is the
        worst outcome this road has."""
        assert mod.floor_config_results(floor_body(uid="pac-flash-2"),
                                        UID) is None

    def test_a_missing_uid_is_refused(self):
        body = floor_body()
        del body["machine_uid"]
        assert mod.floor_config_results(body, UID) is None

    @pytest.mark.parametrize("key", ["corrections", "qc_samples", "qc_targets",
                                     "qc_specs", "maintenance", "override"])
    def test_a_body_missing_any_key_is_refused_whole(self, key):
        """Not partially used. A missing `qc_specs` taken as an empty list is
        "every QC assignment was deleted", which reads as an unmonitored bench
        rather than as a floor that could not answer."""
        body = floor_body()
        del body[key]
        assert mod.floor_config_results(body, UID) is None, \
            f"a body with no {key} was used anyway"

    @pytest.mark.parametrize("key", ["corrections", "qc_samples", "qc_targets",
                                     "qc_specs", "maintenance"])
    def test_a_row_set_that_is_not_a_list_is_refused(self, key):
        assert mod.floor_config_results(floor_body(**{key: "nope"}),
                                        UID) is None

    def test_an_override_that_is_not_a_string_is_refused(self):
        assert mod.floor_config_results(floor_body(override=["service"]),
                                        UID) is None

    def test_an_empty_override_is_a_perfectly_good_answer(self):
        """The overwhelmingly common case: nobody has overridden this bench.
        Refusing a falsy value here would mean every bench in the lab falls
        back to LabCore every window."""
        assert mod.floor_config_results(floor_body(override=""), UID) is not None

    def test_junk_is_refused_and_never_raises(self):
        for body in (None, [], "stale", 3, True, {}, {"error": "cold",
                                                      "stale": True}):
            assert mod.floor_config_results(body, UID) is None


# ── (C) The floor is the preferred source, and it costs LabCore nothing ─────


class TestAHealthyFloorAnswersInsteadOfLabCore:
    def test_a_whole_poll_costs_zero_labcore_config_reads(self, bench):
        """The measurement this task exists for. Five reads per bench per
        window becomes none — the floor's one 12s refresh serves the whole
        building."""
        labcore, floor = LabCore(), FloorServer()
        module = bench(labcore, floor)
        poll(module, NOW)
        assert labcore.config_reads() == [], (
            f"the bench still asked LabCore for {labcore.config_reads()} "
            "while the floor was answering")
        assert floor.calls, "the floor was never asked"

    def test_the_bench_is_actually_configured_from_it(self, bench):
        labcore, floor = LabCore(), FloorServer()
        module = bench(labcore, floor)
        poll(module, NOW)
        machine = module._machine
        assert machine.corrections == {"Flash": -3.0, "Density": 0.0012}
        assert sorted(s.name for s in machine.tests) == ["Density", "Flash"]
        assert [t.name for t in machine.maintenance] == ["Annual calibration"]
        assert machine.manual_override == OVERRIDE

    def test_it_is_asked_with_this_bench_s_own_uid_and_token(self, bench):
        labcore, floor = LabCore(), FloorServer()
        module = bench(labcore, floor)
        poll(module, NOW)
        url, token, uid, _timeout = floor.calls[0]
        assert (url, token, uid) == (FLOOR, "tok", UID)

    def test_the_corrections_reach_the_measurement_itself(self, bench):
        """Not just the map. A correction served from the floor has to be
        added to every reading before it is written, shown or QC-judged —
        ISO/IEC 17025 section 7.8.2 does not care where the number came
        from."""
        labcore, floor = LabCore(), FloorServer()
        module = bench(labcore, floor)
        payload = module._process_outcome(
            module._machine, [], None, [], NOW,
            manual_rows=[{LAB_ID_KEY: "STD-100", "Flash": 66.5}])
        assert payload["rows"][0]["Flash"] == pytest.approx(63.5)

    def test_the_window_is_still_stamped(self, bench):
        """A floor answer IS an answer. Not stamping it would ask the floor
        again on every poll — cheap, but it also means the corrections road
        never settles and the honest accounting below stops meaning
        anything."""
        labcore, floor = LabCore(), FloorServer()
        module = bench(labcore, floor)
        poll(module, NOW)
        assert module._config_read_at == NOW
        assert module._corrections_read_at == NOW

    def test_it_is_not_asked_again_inside_the_window(self, bench):
        """The floor is cheap, not free — it is still a request per bench per
        poll, and the schedule is what this task explicitly does not change."""
        labcore, floor = LabCore(), FloorServer()
        module = bench(labcore, floor)
        poll(module, NOW)
        before = len(floor.calls)
        poll(module, NOW + timedelta(seconds=30))
        assert len(floor.calls) == before, (
            "the floor was re-asked inside the refresh window")

    def test_the_window_still_reopens(self, bench):
        """Cached, not frozen — QC assigned on the floor has to reach the
        bench."""
        labcore, floor = LabCore(), FloorServer()
        module = bench(labcore, floor)
        poll(module, NOW)
        before = len(floor.calls)
        poll(module, NOW + timedelta(
            seconds=mod.CONFIG_REFRESH_SECONDS + 1))
        assert len(floor.calls) > before


# ── (D) Every way the floor can fail ends at LabCore ────────────────────────
#
# The whole safety story, one branch at a time. Each of these must (1) fall
# back, and (2) still leave the bench correctly configured — a fallback that
# reads LabCore and then drops the rows on the floor is not a fallback.


class TestEveryFailureFallsBackToLabCore:
    def _fell_back(self, labcore, module):
        assert "qc_samples" in labcore.reads, "LabCore was never asked"
        assert "override" in labcore.reads
        assert configured(module._machine) == {
            "corrections": {"Flash": -3.0, "Density": 0.0012},
            "tests": [s.to_dict() for s in module._machine.tests],
            "maintenance": [t.to_dict() for t in module._machine.maintenance],
            "override": OVERRIDE}
        assert [t.name for t in module._machine.maintenance] == [
            "Annual calibration"]
        assert sorted(s.name for s in module._machine.tests) == [
            "Density", "Flash"]

    def test_an_unhealthy_note_channel_never_even_asks(self, bench):
        """`_live_channel_healthy()` is the gate, and it is the right one: it
        is already what says whether there is a floor talking to this bench at
        all. Asking a floor the note road has given up on would put a 1.5s
        timeout on every poll of every bench."""
        labcore, floor = LabCore(), FloorServer()
        module = bench(labcore, floor, healthy=False)
        poll(module, NOW)
        assert floor.calls == [], (
            "the bench asked a floor its own note channel says is not there")
        self._fell_back(labcore, module)

    def test_no_live_url_published_never_asks(self, bench):
        """A lab that has not set the live road up — which is every lab until
        somebody does."""
        labcore = LabCore(live_url="")
        floor = FloorServer()
        module = bench(labcore, floor, healthy=False)
        poll(module, NOW)
        assert floor.calls == []
        self._fell_back(labcore, module)

    def test_no_answer_at_all_falls_back(self, bench):
        """An unreachable floor: rebooting, cable out, laptop asleep."""
        labcore, floor = LabCore(), FloorServer(body=None)
        module = bench(labcore, floor)
        poll(module, NOW)
        assert floor.calls, "the floor was never asked"
        self._fell_back(labcore, module)

    def test_a_non_dict_body_falls_back(self, bench):
        labcore, floor = LabCore(), FloorServer(body=["not", "a", "body"])
        module = bench(labcore, floor)
        poll(module, NOW)
        self._fell_back(labcore, module)

    def test_a_body_missing_the_keys_falls_back(self, bench):
        labcore = LabCore()
        floor = FloorServer(body={"machine_uid": UID,
                                  "snapshot_age_seconds": 1.0})
        module = bench(labcore, floor)
        poll(module, NOW)
        self._fell_back(labcore, module)

    def test_a_snapshot_older_than_the_bound_falls_back(self, bench):
        labcore = LabCore()
        floor = FloorServer(body=floor_body(
            age=mod.FLOOR_CONFIG_MAX_AGE_SECONDS + 1))
        module = bench(labcore, floor)
        poll(module, NOW)
        self._fell_back(labcore, module)

    def test_a_missing_age_falls_back(self, bench):
        labcore = LabCore()
        body = floor_body()
        del body["snapshot_age_seconds"]
        floor = FloorServer(body=body)
        module = bench(labcore, floor)
        poll(module, NOW)
        self._fell_back(labcore, module)

    def test_a_non_numeric_age_falls_back(self, bench):
        labcore = LabCore()
        floor = FloorServer(body=floor_body(age="very fresh"))
        module = bench(labcore, floor)
        poll(module, NOW)
        self._fell_back(labcore, module)

    def test_an_answer_about_another_bench_falls_back(self, bench):
        labcore = LabCore()
        floor = FloorServer(body=floor_body(uid="somebody-else"))
        module = bench(labcore, floor)
        poll(module, NOW)
        self._fell_back(labcore, module)

    def test_a_floor_that_raises_falls_back_and_the_poll_survives(self, bench):
        """`fetch_floor_config` swallows everything, but the caller cannot
        assume that of a future edit to it — a raise here strands `_polling`
        and the bench stops polling at all."""
        labcore = LabCore()
        floor = FloorServer(boom=RuntimeError("the floor exploded"))
        module = bench(labcore, floor)
        payload = poll(module, NOW)
        assert payload is not None, "the poll did not come back"
        self._fell_back(labcore, module)

    def test_a_rejected_token_over_the_real_wire_falls_back(self, bench, wire):
        """401, end to end through the real `fetch_floor_config`."""
        labcore = LabCore()
        module = bench(labcore)
        wire(Response(boom=mod.urllib.error.HTTPError(
            FLOOR, 401, "Unauthorized", {}, None)))
        poll(module, NOW)
        self._fell_back(labcore, module)

    def test_a_cold_snapshot_over_the_real_wire_falls_back(self, bench, wire):
        """503 + {"error": ..., "stale": true} — the floor has restarted and
        has nothing to serve yet."""
        labcore = LabCore()
        module = bench(labcore)
        wire(Response(boom=mod.urllib.error.HTTPError(
            FLOOR, 503, "Service Unavailable", {}, None)))
        poll(module, NOW)
        self._fell_back(labcore, module)

    def test_a_proxy_login_page_over_the_real_wire_falls_back(self, bench,
                                                              wire):
        labcore = LabCore()
        module = bench(labcore)
        wire(Response("<html><body>Please sign in</body></html>"))
        poll(module, NOW)
        self._fell_back(labcore, module)


class TestTheAccountingStaysHonest:
    def test_a_floor_failure_over_a_good_labcore_read_is_an_answer(self, bench):
        """The floor is an accelerator. Falling back and succeeding is a read
        that happened, and re-asking on the next poll would throw away the
        window this module already earned."""
        labcore, floor = LabCore(), FloorServer(body=None)
        module = bench(labcore, floor)
        poll(module, NOW)
        assert module._config_read_at == NOW
        assert module._corrections_read_at == NOW

    def test_a_floor_failure_over_a_refused_labcore_read_is_not(self, bench):
        """Neither source answered. Caching that would leave the bench running
        for the whole window on configuration it never received — the exact
        failure `_config_due` was written against, arriving by a new road."""
        labcore = LabCore()
        labcore.refuse = {"qc_samples", "targets", "qc_specs", "maint",
                          "corrections", "override"}
        floor = FloorServer(body=None)
        module = bench(labcore, floor)
        poll(module, NOW)
        assert module._config_read_at is None, (
            "a poll where nothing answered stamped the config window")
        assert module._corrections_read_at is None
        assert module._override_read_at is None

    def test_and_the_next_poll_tries_again(self, bench):
        labcore = LabCore()
        labcore.refuse = {"qc_samples", "targets", "qc_specs", "maint",
                          "corrections", "override"}
        floor = FloorServer(body=None)
        module = bench(labcore, floor)
        poll(module, NOW)
        before = len(floor.calls)
        labcore.refuse = set()
        poll(module, NOW + timedelta(seconds=30))
        assert len(floor.calls) > before, "the floor was not re-asked"
        assert labcore.count("qc_samples") == 2

    def test_a_partial_labcore_answer_is_still_not_a_config(self, bench):
        """Unchanged behaviour, restated here because the floor path now sits
        beside it: half a configuration must not stamp the window."""
        labcore = LabCore()
        labcore.refuse = {"maint"}
        floor = FloorServer(body=None)
        module = bench(labcore, floor)
        poll(module, NOW)
        assert module._config_read_at is None


# ── (E) Driven by the real note road, end to end ────────────────────────────


class TestTheChannelDecidesWithoutBeingToldTo:
    def test_the_first_poll_of_a_module_life_uses_labcore(self, bench):
        """Nothing has proved the channel yet — `_live_delivering` is only
        raised by a push that came back speaking the note protocol, and the
        first push happens at the END of this poll. Reading LabCore once at
        start-up is the correct and safe answer."""
        labcore, floor = LabCore(), FloorServer()
        module = bench(labcore, floor, healthy=False)
        poll(module, NOW)
        assert floor.calls == []
        assert "qc_samples" in labcore.reads

    def test_and_the_poll_after_it_uses_the_floor(self, bench):
        """The push at the end of poll one earns the channel."""
        labcore, floor = LabCore(), FloorServer()
        module = bench(labcore, floor, healthy=False)
        poll(module, NOW)
        assert module._live_channel_healthy() is True
        before = list(labcore.config_reads())
        poll(module, NOW + timedelta(seconds=mod.CONFIG_REFRESH_SECONDS + 1))
        assert floor.calls, "the proven channel was not used"
        assert labcore.config_reads() == before, (
            "LabCore was asked again once the floor was known to be there")

    def test_a_floor_that_dies_mid_shift_goes_back_to_labcore(self, bench,
                                                              monkeypatch):
        """The note road notices first — the push stops coming back — and this
        road follows it, because they share one gate."""
        labcore, floor = LabCore(), FloorServer()
        module = bench(labcore, floor)
        poll(module, NOW)
        assert labcore.config_reads() == []
        monkeypatch.setattr(mod, "post_live", lambda *a, **kw: None)
        for i in range(mod.LIVE_RETRY_AFTER + 1):
            poll(module, NOW + timedelta(
                seconds=(mod.CONFIG_REFRESH_SECONDS + 1) * (i + 1)))
        assert "qc_samples" in labcore.reads, (
            "the floor stopped answering and the bench never went back to "
            "LabCore for its configuration")


# ── (F) The two roads must agree, on the same input ─────────────────────────


class TestTheFloorAnswerGoesThroughTheSameParsers:
    """The most valuable test here.

    The floor road exists to change the SOURCE of a read, never its meaning.
    If the two roads can produce different configurations from identical rows,
    then there are two implementations of what a bench believes about itself
    and the disagreement is invisible: both benches look configured, and only
    the numbers they report differ.

    So this is a genuine same-input/same-output comparison — one set of rows,
    both roads, one equality — and NOT two hand-written expectations, which can
    agree with each other while both being wrong.
    """

    def test_identical_rows_leave_the_bench_in_an_identical_state(self, bench):
        by_floor = bench(LabCore(), FloorServer(),
                         machine=Machine(uid=UID, title="Eraspec",
                                         source_type="manual"))
        poll(by_floor, NOW)

        labcore = LabCore()
        by_labcore = bench(labcore, FloorServer(body=None),
                           machine=Machine(uid=UID, title="Eraspec",
                                           source_type="manual"))
        poll(by_labcore, NOW)

        assert labcore.config_reads(), "the control arm never read LabCore"
        assert configured(by_floor._machine) == configured(
            by_labcore._machine), (
            "the same rows produced a different bench depending on which road "
            "carried them")

    def test_it_would_notice_a_difference(self, bench):
        """The counterweight. An equality that cannot fail proves nothing, so
        feed one road a different QC assignment and watch it separate."""
        by_floor = bench(LabCore(), FloorServer(body=floor_body(
            qc_targets=[])))
        poll(by_floor, NOW)
        by_labcore = bench(LabCore(), FloorServer(body=None))
        poll(by_labcore, NOW)
        assert configured(by_floor._machine) != configured(
            by_labcore._machine)

    def test_an_empty_floor_answer_clears_what_an_empty_labcore_read_does(
            self, bench):
        """Deleting a correction has to actually stop it correcting, whichever
        road brought the news. An empty LIST is a real answer on both."""
        empty = {"corrections": [], "qc_samples": [], "qc_targets": [],
                 "qc_specs": [], "maintenance": [], "override": ""}
        by_floor = bench(LabCore(), FloorServer(body=floor_body(**empty)))
        poll(by_floor, NOW)

        class EmptyLabCore(LabCore):
            def _answer(self, tag, rows):
                return super()._answer(tag, [])

        by_labcore = bench(EmptyLabCore(), FloorServer(body=None))
        poll(by_labcore, NOW)
        assert configured(by_floor._machine) == configured(
            by_labcore._machine)
        assert by_floor._machine.corrections == {}


# ── (G) The in-flight guard covers the floor road too ───────────────────────
#
# `_corrections_epoch` exists because the corrections read runs on the WORKER
# and the GUI thread can bind a different instrument, or save a correction,
# while it is in flight. That is not a property of LabCore being slow — it is a
# property of the read being off-thread — so a floor answer is exposed to
# exactly the same race and must be discarded in exactly the same way.


class TestAFloorAnswerInFlightIsNeverBelievedAboutSomethingElse:
    def test_a_new_instrument_bound_mid_read_discards_the_answer(self, bench):
        """The one that reports RAW values. `Machine.to_dict` does not carry
        `corrections`, so a newly bound bench starts with none and this read is
        the only thing that fills them in — stamping the window on the way past
        leaves it reporting uncorrected results for the whole window."""
        labcore, floor = LabCore(), FloorServer()
        stale = Machine(uid=UID, title="Eraspec", source_type="manual")
        module = bench(labcore, floor, machine=stale)
        bound = Machine(uid="m2", title="PAC Flash 2", source_type="manual")
        floor.during = lambda: module.set_machine(bound, publish=False)

        poll(module, NOW)

        assert module._corrections_read_at is None, (
            "a floor answer that began before the instrument changed stamped "
            "the window for the one bound after it")
        assert stale.corrections == {}, (
            "the floor's answer was applied to the instrument that is no "
            "longer bound")

    def test_an_operator_save_mid_read_discards_the_answer(self, bench,
                                                           monkeypatch):
        """The floor's snapshot is up to 12s old, so it is still carrying the
        PRE-edit factor. Applied, it silently reverts the operator's own save
        and then caches the reversion for the whole window."""
        labcore, floor = LabCore(), FloorServer()
        module = bench(labcore, floor)

        def save():
            mod.apply_corrections(module._machine, {"Flash": -4.0})
            module._corrections_read_at = None
            module._corrections_epoch += 1

        floor.during = save
        poll(module, NOW)

        assert module._machine.corrections["Flash"] == pytest.approx(-4.0), (
            "the pre-edit factor the floor was still holding reverted the "
            "operator's own save")
        assert module._corrections_read_at is None, (
            "and then cached the reverted factor for the whole window")

    def test_an_undisturbed_floor_answer_is_still_believed(self, bench):
        """The counterweight: a guard that discarded every answer would turn
        the corrections off entirely."""
        labcore, floor = LabCore(), FloorServer()
        module = bench(labcore, floor)
        poll(module, NOW)
        assert module._corrections_read_at == NOW
        assert module._machine.corrections["Flash"] == pytest.approx(-3.0)


# ── (H) Against the server's real output ────────────────────────────────────
#
# Three things the finished web server does that a hand-rolled fixture is very
# likely to get wrong, each of which fails SILENTLY — the bench looks
# configured and is holding the wrong thing, or the safety gate is open.

SERVER_JSON = json.dumps({
    "machine_uid": "pac-flash-2",
    "snapshot_age_seconds": 0.002022,
    "override": "SERVICE",
    "corrections": [{"test_name": "Flash Point", "correction": -3.0}],
    "qc_samples": [{"name": "Flash CRM", "sample_id_val": "L-9001",
                    "tests": "[{\"name\": \"Flash Point\"}]"}],
    "qc_targets": [{"sample_name": "Flash CRM", "test_name": "Flash Point"}],
    "qc_specs": [{"machine_uid": "pac-flash-2", "test_name": "Flash Point",
                  "sample_id": "L-9001", "expected": 62.5, "std_dev": 0.8,
                  "k": 2.0, "units": "C"}],
    "maintenance": [{"uid": "t-1", "name": "Annual cal",
                     "kind": "calibration", "interval_days": 365,
                     "last_done": "2026-01-04", "note": "send out"}]})


class TestANullSnapshotAgeIsRefused:
    """`snapshot_age_seconds` arrives as JSON `null` when the server's
    timestamp is unset. Its `ready` gate makes that unreachable today and is
    one refactor away from not being — and a client that reads `null` as
    "fresh" has silently switched the entire staleness net off, which is how a
    bench ends up applying a superseded calibration offset to every measurement
    it reports (ISO/IEC 17025 section 7.8.2).

    Called out separately from the missing key: `None` is a value that is
    PRESENT, so a check written as `"snapshot_age_seconds" in body` would pass
    it, and that is a plausible way for someone to rewrite this gate.
    """

    def test_a_null_age_is_not_a_fresh_one(self):
        assert mod.floor_config_results(floor_body(age=None), UID) is None

    def test_a_null_age_over_the_real_wire_is_refused(self, wire):
        """End to end: JSON `null` -> Python None -> unusable. `fetch_floor_config`
        still hands the body back (it is a well-formed 200), and the gate is
        what refuses it — so the refusal cannot be lost by a change to either
        half alone."""
        body = floor_body(age=None)
        wire(Response(json.dumps(body)))
        fetched = mod.fetch_floor_config(FLOOR, "tok", UID)
        assert fetched == body, "the GET should hand a well-formed 200 back"
        assert mod.floor_config_results(fetched, UID) is None

    def test_a_bench_served_a_null_age_reads_labcore_instead(self, bench):
        labcore = LabCore()
        floor = FloorServer(body=floor_body(age=None))
        module = bench(labcore, floor)
        poll(module, NOW)
        assert "qc_samples" in labcore.reads, (
            "a null snapshot age was taken as fresh and the bench ran on it")
        assert module._machine.corrections == {"Flash": -3.0,
                                               "Density": 0.0012}


class TestTheServersOwnOutput:
    def test_the_qc_sample_tests_column_is_a_json_string_not_a_list(self):
        """`parse_qc_sample_rows` calls `json.loads` on it, because that is how
        the column is stored. A fixture holding a pre-parsed list would prove
        the parser works on something the server never sends — and the real
        answer would come back with no QC at all."""
        assert isinstance(QC_SAMPLE_ROWS[0]["tests"], str)
        assert isinstance(
            json.loads(SERVER_JSON)["qc_samples"][0]["tests"], str)

    def test_the_verbatim_server_body_configures_a_bench(self, bench, wire):
        """The exact JSON the server emits, through the real GET, the real
        gate and the real parsers. Nothing here is reshaped by the test."""
        uid = "pac-flash-2"
        labcore = LabCore()
        module = bench(labcore, machine=Machine(uid=uid, title="PAC Flash 2",
                                                source_type="manual"))
        wire(Response(SERVER_JSON))
        poll(module, NOW)

        machine = module._machine
        assert labcore.config_reads() == [], (
            "the server's own answer was refused and LabCore paid for it")
        assert machine.corrections == {"Flash Point": -3.0}
        # The per-machine `lem_qc_specs` row wins over the standard's own
        # figures, exactly as it does on the LabCore road.
        assert [(s.name, s.expected, s.std_dev, s.units, s.sample_id)
                for s in machine.tests] == [
                    ("Flash Point", 62.5, 0.8, "C", "L-9001")]
        assert [(t.uid, t.name, t.kind, t.interval_days)
                for t in machine.maintenance] == [
                    ("t-1", "Annual cal", "calibration", 365)]
        assert machine.manual_override == mod.STATUS_SERVICE

    def test_an_uppercase_service_override_is_the_one_the_module_knows(self):
        """The server sends "SERVICE" verbatim, and `extract_overrides` drops
        anything outside `_VALID_OVERRIDES` — so a mismatch here would not be
        an error, it would be a lever that silently never moves."""
        assert mod.STATUS_SERVICE == "SERVICE"
        results = mod.floor_config_results(
            floor_body(override="SERVICE"), UID)
        assert mod.extract_overrides(results["override"]["rows"]) == {
            UID: mod.STATUS_SERVICE}

    def test_a_null_numeric_column_is_survived_not_choked_on(self, bench):
        """A genuinely NULL numeric column comes back as `null`, not 0.0.
        These rows reach the parsers on the WORKER, where a raise strands
        `_polling` and the bench stops polling altogether — so every one of
        them has to be skipped or defaulted, never raised.

        Note what each one does, because they are deliberately different: a
        null CORRECTION is dropped rather than defaulted to 0.0, since
        inventing an offset is worse than having none; a null spec figure drops
        the whole spec, since a band with no number cannot judge anything; a
        null interval falls back to the schedule default.
        """
        labcore = LabCore()
        floor = FloorServer(body=floor_body(
            corrections=[{"test_name": "Flash", "correction": None},
                         {"test_name": "Density", "correction": 0.0012}],
            qc_specs=[{"machine_uid": UID, "test_name": "Density",
                       "sample_id": "STD-100", "expected": None,
                       "std_dev": None, "k": 2.0, "units": None}],
            maintenance=[{"uid": "pm-1", "name": "Annual calibration",
                          "kind": "calibration", "interval_days": None,
                          "last_done": None, "note": None}]))
        module = bench(labcore, floor)
        payload = poll(module, NOW)

        assert payload is not None, "a null column stranded the poll"
        assert module._machine.corrections == {"Density": 0.0012}
        assert all(s.name != "Density" for s in module._machine.tests), (
            "a QC spec with no expected value was kept as a band to judge on")
        assert [t.interval_days for t in module._machine.maintenance] == [30]
