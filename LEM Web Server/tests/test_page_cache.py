"""The last per-request round-trips: checklists, and the log's filter list.

After the floor, the schedule and the fleet maintenance moved onto the background
snapshot, two pages were still paying LabCore on every request:

* **Checklists** — the definitions (which change perhaps monthly) and the day's
  ticks, re-read by every poll of every open screen.
* **Logs** — `SELECT DISTINCT kind FROM lem_machine_log`, just to populate a
  filter dropdown of about six fixed words. On the live table this is the same
  shape of query that once took eight seconds.

These are cached in the server rather than snapshotted, because unlike the floor
they have exactly one writer: this process. Nobody else ticks a checklist or
renames a log kind. That makes invalidation *exact* — every write that could
change an answer drops it — so the cache is not a staleness trade-off at all,
and an operator can never fail to see their own tick.

It also replaces the dead code it grew out of: a 4-second machine cache whose
getter and setter were no longer called by anything, while eight `_invalidate()`
calls still dutifully cleared a key nobody read.
"""
import threading
import time

import pytest

from labcore_gateway import FakeLabCoreGateway


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


class Counting(FakeLabCoreGateway):
    def __init__(self):
        super().__init__()
        self.reads = []
        self.lock = threading.Lock()

    def read_sql(self, sql, args=None, **kw):
        with self.lock:
            self.reads.append(" ".join(sql.split()))
        return super().read_sql(sql, args, **kw)

    def is_running(self):
        return True


@pytest.fixture
def gw():
    return Counting()


@pytest.fixture
def client(gw):
    from web_app import create_app
    app = create_app(gw, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client()


def signed_in(client):
    client.post("/api/login", json={"username": "k", "password": "good"})
    return client


def a_checklist(client, name="Opening", items=None):
    return client.post("/api/checklists", json={
        "name": name, "slot": "opening", "due_time": "09:30",
        "items": items or [{"text": "Check helium", "entry_type": "number",
                            "units": "PSI"},
                           {"text": "Empty waste", "entry_type": "none"}]})


# ── checklists ──────────────────────────────────────────────────────────────

class TestChecklistsAreCached:
    def test_repeat_reads_cost_nothing(self, gw, client):
        signed_in(client)
        a_checklist(client)
        client.get("/api/checklists")
        gw.reads.clear()
        for _ in range(10):
            assert client.get("/api/checklists").status_code == 200
        assert gw.reads == [], f"{len(gw.reads)} ops for 10 checklist reads"

    def test_the_answer_is_unchanged(self, gw, client):
        signed_in(client)
        a_checklist(client)
        body = client.get("/api/checklists").get_json()
        cl = body["checklists"][0]
        assert cl["name"] == "Opening"
        assert [i["text"] for i in cl["items"]] == ["Check helium", "Empty waste"]
        assert cl["total"] == 2 and cl["checked"] == 0

    def test_a_tick_shows_immediately(self, gw, client):
        """The operator must see their own tick — this is why invalidation is
        explicit rather than a TTL."""
        signed_in(client)
        a_checklist(client)
        body = client.get("/api/checklists").get_json()
        cl = body["checklists"][0]
        item = cl["items"][0]["uid"]
        client.post(f"/api/checklists/{cl['uid']}/toggle",
                    json={"item_uid": item, "checked": True})
        after = client.get("/api/checklists").get_json()
        assert after["checklists"][0]["checked"] == 1
        assert after["state"][cl["uid"]][item]["checked"] is True

    def test_a_recorded_value_shows_immediately(self, gw, client):
        signed_in(client)
        a_checklist(client)
        cl = client.get("/api/checklists").get_json()["checklists"][0]
        item = cl["items"][0]["uid"]
        client.post(f"/api/checklists/{cl['uid']}/value",
                    json={"item_uid": item, "value": "2900"})
        state = client.get("/api/checklists").get_json()["state"]
        assert state[cl["uid"]][item]["value"] == "2900"

    def test_an_edited_checklist_shows_immediately(self, gw, client):
        signed_in(client)
        a_checklist(client)
        cl = client.get("/api/checklists").get_json()["checklists"][0]
        cl["name"] = "Opening (revised)"
        client.post("/api/checklists", json=cl)
        names = [c["name"] for c in
                 client.get("/api/checklists").get_json()["checklists"]]
        assert names == ["Opening (revised)"]

    def test_a_deleted_checklist_disappears_immediately(self, gw, client):
        signed_in(client)
        a_checklist(client)
        cl = client.get("/api/checklists").get_json()["checklists"][0]
        client.delete(f"/api/checklists/{cl['uid']}")
        assert client.get("/api/checklists").get_json()["checklists"] == []

    def test_each_day_is_cached_separately(self, gw, client):
        """Yesterday's ticks must not leak into today's, cached or not."""
        signed_in(client)
        a_checklist(client)
        cl = client.get("/api/checklists").get_json()["checklists"][0]
        item = cl["items"][0]["uid"]
        client.post(f"/api/checklists/{cl['uid']}/toggle",
                    json={"item_uid": item, "checked": True})
        client.get("/api/checklists?day=2026-07-01")
        today = client.get("/api/checklists").get_json()
        past = client.get("/api/checklists?day=2026-07-01").get_json()
        assert today["checklists"][0]["checked"] == 1
        assert past["checklists"][0]["checked"] == 0

    def test_a_tick_does_not_wipe_another_days_cache_needlessly(self, gw, client):
        signed_in(client)
        a_checklist(client)
        cl = client.get("/api/checklists").get_json()["checklists"][0]
        client.get("/api/checklists?day=2026-07-01")     # warm the old day
        client.post(f"/api/checklists/{cl['uid']}/toggle",
                    json={"item_uid": cl["items"][0]["uid"], "checked": True})
        gw.reads.clear()
        client.get("/api/checklists?day=2026-07-01")
        assert gw.reads == [], "ticking today re-read an unrelated day"


# ── the log filter list ─────────────────────────────────────────────────────

class TestLogKindsAreCached:
    def seed_log(self, gw):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
               "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
               "detail TEXT)")
        gw.sql("INSERT INTO lem_machine_log VALUES "
               "('m1','2026-08-03T09:00:00','run','1','CP','-7.4','{}')")

    def kind_reads(self, gw):
        return [s for s in gw.reads if "DISTINCT kind" in s]

    def test_the_distinct_scan_happens_once_not_per_request(self, gw, client):
        self.seed_log(gw)
        client.get("/api/logs")
        gw.reads.clear()
        for _ in range(10):
            client.get("/api/logs")
        assert self.kind_reads(gw) == [], "scanned the log table for every viewer"

    def test_the_kinds_are_still_reported(self, gw, client):
        self.seed_log(gw)
        assert "run" in client.get("/api/logs").get_json()["kinds"]

    def test_a_new_kind_appears_once_the_server_writes_one(self, gw, client):
        """The server audits config changes into the same table, so its own
        writes must not be hidden by its own cache."""
        self.seed_log(gw)
        signed_in(client)
        assert "config" not in client.get("/api/logs").get_json()["kinds"]
        a_checklist(client)            # audited as kind='config'
        assert "config" in client.get("/api/logs").get_json()["kinds"]


# ── the dead cache is gone ──────────────────────────────────────────────────

class TestNoDeadCache:
    def test_the_unused_machine_cache_helpers_are_gone(self):
        """`_snapshot_cache_get`/`_put` had no callers left once the floor moved
        to the snapshot service, while eight `_invalidate()` calls still cleared
        the key they wrote. Dead code that looks live is worse than none."""
        import pathlib
        src = pathlib.Path(__file__).resolve().parent.parent / "web_app.py"
        text = src.read_text(encoding="utf-8")
        assert "_snapshot_cache_get" not in text
        assert "_snapshot_cache_put" not in text

    def test_the_superseded_parallel_reader_is_gone(self):
        """`_gather()` ran ten reads at once — a real improvement over ten in
        series, and completely pointless once requests stopped reading at all.
        Left in place it would read as the live strategy."""
        import pathlib
        src = pathlib.Path(__file__).resolve().parent.parent / "web_app.py"
        text = src.read_text(encoding="utf-8")
        assert "def _gather" not in text
        assert "SNAPSHOT_TTL_SECONDS" not in text
        assert "LABCORE_READ_WORKERS" not in text


# ── the machine list ────────────────────────────────────────────────────────

class TestMachineListComesFromTheSnapshot:
    """`_machine_list()` was memoised per request, which fixed asking three times
    in one request but not asking once in every request. The snapshot reads
    `lem_machine_status` every cycle anyway — that is where uid→title belongs."""

    def seed(self, gw):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
               "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
               "reason TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO lem_machine_status VALUES "
               "('m1','OptiMPP 1','GREEN','ok','2026-08-03T09:00:00')")

    def status_reads(self, gw):
        return [s for s in gw.reads
                if "lem_machine_status" in s and "UNION ALL" not in s]

    def test_the_logs_page_does_not_re_read_it(self, gw, client):
        self.seed(gw)
        client.get("/api/logs")
        gw.reads.clear()
        client.get("/api/logs")
        assert self.status_reads(gw) == []

    def test_the_fleet_maintenance_does_not_re_read_it(self, gw, client):
        self.seed(gw)
        client.get("/api/maintenance")
        gw.reads.clear()
        client.get("/api/maintenance")
        assert self.status_reads(gw) == []

    def test_the_titles_are_right(self, gw, client):
        self.seed(gw)
        signed_in(client)
        client.post("/api/machines/m1/maintenance",
                    json={"name": "Clean", "kind": "pm", "interval_days": 7,
                          "last_done": "2026-08-01"})
        row = client.get("/api/maintenance").get_json()["tasks"][0]
        assert row["machine_title"] == "OptiMPP 1"

    def test_a_newly_registered_machine_is_named_not_dropped(self, gw, client):
        """A module registering between refreshes must not be nameless — the
        snapshot is a cache of names, not the authority on which exist."""
        self.seed(gw)
        client.get("/api/logs")
        gw.sql("INSERT INTO lem_machine_status VALUES "
               "('m2','Newcomer','GREEN','ok','2026-08-03T10:00:00')")
        titles = client.get("/api/machines?fresh=1").get_json()["machines"]
        assert "Newcomer" in [m["title"] for m in titles]


# ── an empty table is an answer ─────────────────────────────────────────────

class TestEmptyIsNotUnknown:
    """`from_snapshot(...) or read_live()` reads an empty table as "no data yet"
    and pays for a round-trip to confirm it — in exactly the case where there was
    nothing to fetch. The question is whether the snapshot has BUILT, not whether
    what it found was empty."""

    def test_a_lab_with_no_heartbeats_costs_no_extra_op(self, gw, client):
        signed_in(client)
        client.post("/api/machine-configs", json={"title": "Brand new"})
        client.get("/api/machine-configs")
        gw.reads.clear()
        client.get("/api/machine-configs")
        beat_reads = [s for s in gw.reads if "lem_machine_heartbeat" in s
                      and "UNION ALL" not in s]
        assert beat_reads == [], "re-read an empty heartbeat table"

    def test_a_lab_with_no_machines_costs_no_extra_op(self, gw, client):
        client.get("/api/maintenance")
        gw.reads.clear()
        client.get("/api/maintenance")
        assert gw.reads == [], f"{len(gw.reads)} ops for an empty lab"

    def test_configs_are_still_listed(self, gw, client):
        signed_in(client)
        client.post("/api/machine-configs", json={"title": "Brand new"})
        rows = client.get("/api/machine-configs").get_json()["configs"]
        assert [r["title"] for r in rows] == ["Brand new"]
        assert rows[0]["in_use"] is False


# ── the floor's run blips ────────────────────────────────────────────────────

class TestRecentEventsComeFromTheSnapshot:
    """The floor animates a blip when a run lands, by polling `/api/events`
    every six seconds. That was a live LabCore read — **10 ops/min per open
    screen**, more than everything else on the page put together, and the one
    cost that grew with the number of wall displays.

    The snapshot carries the newest events instead, so the blips keep working and
    the poll is free. `?limit=` above what the snapshot holds still reads live —
    the log viewer's deep queries are not the floor's animation.
    """
    EVENT_LIMIT = 60

    def seed(self, gw, n=3):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
               "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
               "detail TEXT)")
        for i in range(n):
            gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
                   ["m1", f"2026-08-03T09:{i:02d}:00", "run", str(i),
                    "Cloud Point", "-7.4", "{}"])

    def test_the_six_second_poll_is_free(self, gw, client):
        self.seed(gw)
        client.get("/api/events?limit=60")
        gw.reads.clear()
        for _ in range(10):
            assert client.get("/api/events?limit=60").status_code == 200
        assert gw.reads == [], f"{len(gw.reads)} ops for 10 blip polls"

    def test_the_events_are_the_same_and_newest_first(self, gw, client):
        self.seed(gw)
        events = client.get("/api/events?limit=60").get_json()["events"]
        assert [e["ts"] for e in events] == ["2026-08-03T09:02:00",
                                             "2026-08-03T09:01:00",
                                             "2026-08-03T09:00:00"]
        assert events[0]["kind"] == "run"
        assert events[0]["machine_uid"] == "m1"
        assert events[0]["test_name"] == "Cloud Point"
        assert events[0]["value"] == "-7.4"
        assert events[0]["lab_id"] == "2"

    def test_the_limit_is_honoured(self, gw, client):
        self.seed(gw, n=5)
        assert len(client.get("/api/events?limit=2").get_json()["events"]) == 2

    def test_asking_for_more_than_the_snapshot_holds_reads_live(self, gw, client):
        self.seed(gw)
        client.get("/api/events?limit=60")
        gw.reads.clear()
        client.get(f"/api/events?limit={self.EVENT_LIMIT + 40}")
        assert gw.reads, "a deep request must not be silently truncated"

    def test_a_new_run_blips_within_a_refresh(self, gw, client):
        """A module writes straight to LabCore, so the blip waits for the next
        refresh — seconds, not the six the client polls at."""
        self.seed(gw)
        client.get("/api/events?limit=60")
        gw.sql("INSERT INTO lem_machine_log VALUES "
               "('m2','2026-08-03T10:00:00','run','9','CP','-8.1','{}')")
        app_snaps = None
        client.get("/api/machines?fresh=1")     # stands in for the poller's tick
        events = client.get("/api/events?limit=60").get_json()["events"]
        assert events[0]["machine_uid"] == "m2"


# ── single-flight ───────────────────────────────────────────────────────────

class TestNoStampede:
    """Flagged in the 2026-08-03 CPU report: nothing stopped concurrent requests
    from all missing the cache at once and each doing the full work.

    `SnapshotService` already single-flights its first build under `_build_lock`.
    `_page` did not: it checked under the lock, released it, then produced — so ten
    viewers arriving on a cold checklist page ran ten identical LabCore reads, each
    paying a TLS setup, in parallel. One thread should do the work and the rest
    should wait for its answer.
    """

    def test_concurrent_misses_produce_once(self, gw, client):
        from web_app import create_app
        calls = []
        gate = threading.Event()

        app = create_app(gw, authenticator=StubAuth(), secret="s")
        app.config["TESTING"] = True
        page = app.config["PAGE"]

        def slow():
            calls.append(1)
            gate.wait(2)
            return {"built": True}

        results = []
        threads = [threading.Thread(
            target=lambda: results.append(page("k", slow))) for _ in range(10)]
        [t.start() for t in threads]
        time.sleep(0.15)
        gate.set()
        [t.join() for t in threads]

        assert len(calls) == 1, f"{len(calls)} threads did the same work"
        assert len(results) == 10
        assert all(r == {"built": True} for r in results)

    def test_a_hit_never_waits_on_a_miss_for_another_key(self):
        """One key building must not block a different key that is already cached
        — a single global lock would turn one slow page into every page."""
        from web_app import create_app
        app = create_app(FakeLabCoreGateway(), authenticator=StubAuth(),
                         secret="s")
        app.config["TESTING"] = True
        page = app.config["PAGE"]
        page("warm", lambda: {"v": 1})
        gate = threading.Event()
        started = threading.Event()

        def slow():
            started.set()
            gate.wait(2)
            return {"v": 2}

        t = threading.Thread(target=lambda: page("cold", slow))
        t.start()
        started.wait(1)
        got = []
        quick = threading.Thread(target=lambda: got.append(page("warm", None)))
        quick.start()
        quick.join(0.5)
        gate.set()
        t.join()
        assert got == [{"v": 1}], "a cached key waited on an unrelated build"

    def test_a_failed_build_does_not_wedge_the_key(self):
        """If the producer raises, the next caller must get to try again rather
        than inherit a permanently held lock."""
        from web_app import create_app
        app = create_app(FakeLabCoreGateway(), authenticator=StubAuth(),
                         secret="s")
        app.config["TESTING"] = True
        page = app.config["PAGE"]

        def boom():
            raise RuntimeError("LabCore down")

        with pytest.raises(RuntimeError):
            page("k", boom)
        assert page("k", lambda: {"ok": True}) == {"ok": True}


# ── the rail has to be scrollable ───────────────────────────────────────────

class TestRailScrolls:
    """Reported 2026-08-03: "the ones that say '3 need attention' in the
    mobile/small window version, theres no way to scroll it down".

    `.rail` had `overflow-y:auto`, which does nothing on its own: it is a grid
    item, and a grid item's automatic minimum size is its CONTENT height. So the
    rail grew past the viewport instead of scrolling inside it, and `.pane`'s
    `overflow:hidden` clipped whatever fell off the bottom. `min-height:0` is what
    lets it shrink and therefore scroll — `.shell` already carried it for exactly
    this reason one level up.
    """

    def floor(self):
        import pathlib
        return (pathlib.Path(__file__).resolve().parent.parent
                / "templates" / "floor.html").read_text(encoding="utf-8")

    def test_the_rail_can_shrink_so_its_overflow_engages(self):
        src = self.floor()
        rail = src[src.index(".rail{"):src.index(".rail.l{")]
        assert "overflow-y:auto" in rail
        assert "min-height:0" in rail, "overflow-y:auto without min-height:0 is inert"

    def test_the_map_stage_can_shrink_too(self):
        src = self.floor()
        stage = src[src.index(".stage{"):src.index(".stage{") + 200]
        assert "min-height:0" in stage

    def test_the_narrow_layout_lets_the_page_scroll(self):
        """Below the breakpoint the whole page scrolls, so the rails must not
        trap it in a nested scroller."""
        src = self.floor()
        mobile = src[src.index("@media (max-width:900px)"):]
        assert "body{overflow:auto}" in mobile
        assert "overflow:visible" in mobile
