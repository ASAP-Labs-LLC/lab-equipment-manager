"""GET /api/ui/live: the global status every open page polls (ia-final §5).

Why each of these is a test and not a hope:

* **0 LabCore ops.** Every open page asks every 3 s. Three tablets, a wall
  and two desks is two requests a second, forever. One read per poll would
  put the lab's screens on the same serialised queue the benches write
  through (~1.5 ops/s), which is the exact load the snapshot exists to
  prevent. `CountingGateway` counts every way into LabCore, including the
  reachability probe, and the answer has to be zero, cold or warm.
* **It never makes LEM look busy.** The updater deploys only when
  `/healthz` `idle_seconds` is high. A poll that counted as a person would
  pin it near zero for as long as any screen is open, and no release would
  ever land unattended, silently (constraints A.7).
* **A failed read is never an empty result.** Before the first snapshot,
  "0 need you" and "0 of 0 benches" would be statements about the lab made
  from no information. They are `None`, and the page draws nothing.
* **One field, one count.** The nav's "6 need you" and a page's count come
  from the same payload field; the first paint uses the same function as
  the poll (`ui_live.nav_meta`), so a reload never shows a different number
  from the poll a second later.
"""
from __future__ import annotations

import time
from datetime import datetime

import pytest

import demo_floor
import ui_live
from labcore_gateway import FakeLabCoreGateway
from live_presence import LivePresence
from web_app import create_app

KEYS = {"cursor", "reset", "machines", "needs_you", "fleet", "round", "qc_out",
        "snapshot_age", "labcore_online", "mirror", "notifications_unread", "jobs",
        "version", "server_now", "lab_tz"}


class CountingGateway(FakeLabCoreGateway):
    """Every road into LabCore, counted: reads, writes and the probe."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def sql(self, *a, **k):
        self.calls.append(("sql", str(a[0])[:50] if a else ""))
        return super().sql(*a, **k)

    def read_sql(self, *a, **k):
        self.calls.append(("read_sql", str(a[0])[:50] if a else ""))
        return super().read_sql(*a, **k)

    def write(self, *a, **k):
        self.calls.append(("write", ""))
        return super().write(*a, **k)

    def is_running(self, *a, **k):
        self.calls.append(("is_running", ""))
        return super().is_running(*a, **k)


class StubAuth:
    def login(self, u, p):
        return ("Kaden Ortiz", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


def _app(gw, tmp_path, live=None):
    app = create_app(gw, authenticator=StubAuth(), secret="s",
                     documents_root=str(tmp_path), live=live or LivePresence(),
                     live_token="test-token")
    app.config["TESTING"] = True
    return app


def _seeded(tmp_path, live=None):
    gw = CountingGateway()
    app = _app(gw, tmp_path, live)
    app.config["SNAPSHOTS"].ensure_schema()
    demo_floor.seed(gw, documents_root=str(tmp_path))
    app.config["SNAPSHOTS"].refresh()
    app.config["WARM"]()                       # today's round into the page cache
    return app, gw


def _machines(app):
    from live_presence import merge_machines
    from web_app import STATUS_COLORS
    snap = app.config["SNAPSHOTS"].get(build_if_missing=False)
    return merge_machines(snap["machines"], app.config["LIVE"], STATUS_COLORS)


# ── cost ────────────────────────────────────────────────────────────────────

class TestItCostsLabCoreNothing:
    def test_warm_polls_with_and_without_a_cursor(self, tmp_path):
        app, gw = _seeded(tmp_path)
        c = app.test_client()
        gw.calls.clear()
        cursor = None
        for _ in range(25):
            r = c.get("/api/ui/live" + ("?since=" + cursor if cursor else ""))
            assert r.status_code == 200
            cursor = r.get_json()["cursor"]
        assert gw.calls == [], gw.calls

    def test_a_cold_server_is_not_made_to_read(self, tmp_path):
        """No snapshot yet: the poll must not be the thing that builds it.
        A dozen screens reconnecting after a restart would otherwise all pay
        for the first build at once."""
        gw = CountingGateway()
        app = _app(gw, tmp_path)
        gw.calls.clear()
        body = app.test_client().get("/api/ui/live").get_json()
        assert gw.calls == [], gw.calls
        assert app.config["SNAPSHOTS"].get(build_if_missing=False)["ready"] is False
        assert KEYS <= set(body)

    def test_a_checklist_tick_does_not_make_the_poll_read(self, tmp_path):
        """A tick drops today's cached round. The poll then serves the last
        round it read, and the re-read happens on a thread, never in the
        poll."""
        app, gw = _seeded(tmp_path)
        _make_round(app)
        c = _signed_in(app)
        cl = c.get("/api/checklists").get_json()["checklists"][0]
        c.post("/api/checklists/%s/toggle" % cl["uid"],
               json={"item_uid": cl["items"][0]["uid"], "checked": True})
        gw.calls.clear()
        for _ in range(5):
            c.get("/api/ui/live")
        assert gw.calls == [], gw.calls


class TestItIsNotAPerson:
    def test_polling_leaves_idle_seconds_rising(self, tmp_path):
        app, _gw = _seeded(tmp_path)
        c = app.test_client()
        first = c.get("/healthz").get_json()["idle_seconds"]
        time.sleep(0.3)
        for _ in range(10):
            c.get("/api/ui/live")
        after = c.get("/healthz").get_json()
        assert after["idle_seconds"] >= first + 0.2, (first, after)
        assert "/api/ui/live" not in after["last_activity"]


# ── the payload ─────────────────────────────────────────────────────────────

class TestThePayload:
    def test_every_field_the_spec_names(self, tmp_path):
        app, _gw = _seeded(tmp_path)
        body = app.test_client().get("/api/ui/live").get_json()
        assert KEYS <= set(body), KEYS - set(body)
        assert body["version"] == app.test_client().get("/healthz").get_json()["version"]
        datetime.fromisoformat(body["server_now"])          # an offset-carrying stamp
        assert "+" in body["server_now"][19:] or "-" in body["server_now"][19:]

    def test_counts_follow_the_readiness_rule(self, tmp_path):
        app, _gw = _seeded(tmp_path)
        body = app.test_client().get("/api/ui/live").get_json()
        ms = _machines(app)
        overrides = ui_live.overrides_from_tables(app.config["SNAPSHOTS"].tables())
        states = [ui_live.readiness(m, (overrides or {}).get(m["machine_uid"], ""))["state"]
                  for m in ms]
        want = sum(1 for s in states if s in ui_live.NEEDS_YOU)
        assert want > 0, "the demo floor should have something that needs you"
        assert body["needs_you"] == want
        out = sum(1 for m in ms for s in m["effective_specs"]
                  if s.get("last_qc_in_spec") is False and not s.get("last_qc_superseded_by"))
        assert body["qc_out"] == out
        assert body["fleet"] == {"checking_in": sum(1 for m in ms if m.get("module_running") or m.get("live")),
                                 "total": len(ms), "live_road": 0}

    def test_nothing_read_yet_is_unknown_not_zero(self, tmp_path):
        gw = CountingGateway()
        app = _app(gw, tmp_path)
        body = app.test_client().get("/api/ui/live").get_json()
        for key in ("needs_you", "fleet", "qc_out", "round", "snapshot_age"):
            assert body[key] is None, (key, body[key])
        assert body["nav_meta"] == {}
        assert body["notifications_unread"] == 0

    def test_the_first_paint_and_the_poll_say_the_same_counts(self, tmp_path):
        app, _gw = _seeded(tmp_path)
        _make_round(app)
        c = _signed_in(app)
        page = c.get("/settings").get_data(as_text=True)
        body = c.get("/api/ui/live").get_json()
        import re
        for key, meta in body["nav_meta"].items():
            m = re.search(r'<span class="nav-meta" data-meta="%s">([^<]*)</span>' % key, page)
            assert m and m.group(1) == meta["text"], (key, meta)
            b = re.search(r'data-badge="%s"[^>]*>([^<]*)</span>' % key, page)
            assert b and b.group(1) == meta["badge"], (key, meta)
        assert set(body["nav_meta"]) >= {"instruments", "qc", "checklists"}


# ── the cursor ──────────────────────────────────────────────────────────────

class TestTheCursor:
    def test_reset_then_quiet_then_the_machine_that_changed(self, tmp_path):
        live = LivePresence()
        app, _gw = _seeded(tmp_path, live)
        c = app.test_client()
        first = c.get("/api/ui/live").get_json()
        assert first["reset"] is True and first["notifications"] is not None
        again = c.get("/api/ui/live?since=" + first["cursor"]).get_json()
        assert again["reset"] is False and again["machines"] == []
        assert "notifications" not in again          # unchanged items do not ride along
        uid = _machines(app)[0]["machine_uid"]
        # a bench pushes RED on the live road: that machine, and only it, changed
        r = c.post("/api/live", json={"machine_uid": uid, "status": "RED", "reason": "x",
                                      "at": datetime.now().isoformat(timespec="seconds")},
                   headers={"X-LEM-Token": "test-token"})
        assert r.status_code in (200, 204), r.get_data(as_text=True)
        moved = c.get("/api/ui/live?since=" + again["cursor"]).get_json()
        assert moved["reset"] is False
        assert moved["machines"] == [uid]
        assert moved["cursor"] != again["cursor"]

    @pytest.mark.parametrize("bad", ["", "garbage", "other:1", "x" * 300, "%s:99999"])
    def test_a_cursor_from_elsewhere_is_a_reset(self, tmp_path, bad):
        app, _gw = _seeded(tmp_path)
        c = app.test_client()
        boot = c.get("/api/ui/live").get_json()["cursor"].split(":")[0]
        bad = bad % boot if "%s" in bad else bad
        assert c.get("/api/ui/live?since=" + bad).get_json()["reset"] is True

    def test_the_feed_ring_overflow_is_a_reset(self):
        f = ui_live.Feed(size=3, boot_id="b")
        f.observe({"a": "1"}, {})
        start = f.cursor()
        for i in range(5):
            f.observe({"a": str(i + 2)}, {})
        assert f.since(start)["reset"] is True
        assert f.since(f.cursor()) == {"cursor": f.cursor(), "reset": False,
                                       "machines": [], "kinds": []}


# ── the round ───────────────────────────────────────────────────────────────

def _signed_in(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = "Kaden Ortiz"
    return c


def _make_round(app):
    c = _signed_in(app)
    for name, slot, due in (("Opening round", "opening", "09:30"),
                            ("Closing round", "closing", "17:00")):
        r = c.post("/api/checklists", json={
            "name": name, "slot": slot, "due_time": due,
            "items": [{"text": "Lights on"}, {"text": "Gas on"}, {"text": "Logbook"}]})
        assert r.status_code == 200, r.get_data(as_text=True)
    app.config["WARM"]()


class TestTheRound:
    def test_the_round_due_next_counts_the_slot(self, tmp_path):
        app, _gw = _seeded(tmp_path)
        _make_round(app)
        body = app.test_client().get("/api/ui/live").get_json()
        assert body["round"]["slot"] == "opening"
        assert (body["round"]["done"], body["round"]["total"]) == (0, 3)
        assert body["nav_meta"]["checklists"] == {"text": "Opening 0/3", "badge": "0/3"}

    def test_a_tick_moves_the_count_without_the_poll_reading(self, tmp_path):
        app, gw = _seeded(tmp_path)
        _make_round(app)
        app.config["LEM_REWARM"] = True
        c = _signed_in(app)
        before = c.get("/api/ui/live").get_json()
        cl = [x for x in c.get("/api/checklists").get_json()["checklists"]
              if x["slot"] == "opening"][0]
        r = c.post("/api/checklists/%s/toggle" % cl["uid"],
                   json={"item_uid": cl["items"][0]["uid"], "checked": True})
        assert r.status_code == 200
        app.config["LEM_REWARM_THREAD"].join(5)
        gw.calls.clear()
        after = c.get("/api/ui/live?since=" + before["cursor"]).get_json()
        assert gw.calls == []
        assert after["round"]["done"] == 1
        assert "round" in after["kinds"]
        assert after["nav_meta"]["checklists"]["text"] == "Opening 1/3"

    def test_round_summary_rules(self):
        now = datetime(2026, 10, 1, 10, 0)
        day = {"checklists": [
            {"slot": "opening", "checked": 3, "total": 3, "due_time": "09:30"},
            {"slot": "closing", "checked": 1, "total": 4, "due_time": "17:00"},
            {"slot": "other", "checked": 0, "total": 9, "due_time": ""}]}
        assert ui_live.round_summary(day, now) == {
            "slot": "closing", "done": 1, "total": 4, "due": "17:00",
            "overdue": False, "complete": False}
        day["checklists"][0]["checked"] = 2
        r = ui_live.round_summary(day, now)
        assert (r["slot"], r["overdue"]) == ("opening", True)
        # two lists in one slot add up: the round is the slot
        two = {"checklists": [{"slot": "opening", "checked": 1, "total": 2, "due_time": "09:30"},
                              {"slot": "opening", "checked": 2, "total": 3, "due_time": "09:00"}]}
        assert ui_live.round_summary(two, now)["done"] == 3
        assert ui_live.round_summary(two, now)["due"] == "09:00"
        done = {"checklists": [{"slot": "opening", "checked": 2, "total": 2, "due_time": ""}]}
        assert ui_live.round_summary(done, now)["complete"] is True
        assert ui_live.nav_meta({"round": ui_live.round_summary(done, now)})["checklists"]["text"] == "Done"
        # unknown is None, a day with no rounds says so
        assert ui_live.round_summary(None, now) is None
        assert ui_live.round_summary({"error": "timeout"}, now) is None
        assert ui_live.round_summary({"checklists": []}, now)["slot"] is None
        assert "checklists" not in ui_live.nav_meta({"round": {"slot": None, "total": 0}})


# ── the bell ────────────────────────────────────────────────────────────────

class TestTheBell:
    def test_the_live_road_down_is_a_sentence(self, tmp_path):
        app, _gw = _seeded(tmp_path)
        body = app.test_client().get("/api/ui/live").get_json()
        n = body["fleet"]["checking_in"]
        road = [x for x in body["notifications"] if x["id"].startswith("liveroad:")]
        assert road and road[0]["message"] == (
            "Benches can't reach LEM directly. 0 of %d use the live road; they read "
            "their settings from LabCore instead." % n)
        assert road[0]["href"] == "/settings#diagnostics"

    def test_every_item_has_a_level_a_time_and_somewhere_to_go(self, tmp_path):
        app, _gw = _seeded(tmp_path)
        notes = app.test_client().get("/api/ui/live").get_json()["notifications"]
        assert notes
        for n in notes:
            assert n["level"] in ("error", "warning", "success", "info")
            assert n["href"] and n["link"] and n["message"]
            datetime.fromisoformat(n["ts"])

    def test_not_ok_then_recovered(self):
        clock = [1000.0]
        board = ui_live.Notices(clock=lambda: clock[0])
        board.remember_links({"gc1": "/floor"})
        item = {"key": "notok:gc1", "level": "error", "message": "GC-1 is not OK to run: x.",
                "href": "/floor", "link": "Open GC-1"}
        titles = {"gc1": "GC-1"}
        first = board.update([item], titles, {"notok": {"gc1"}})
        clock[0] += 60
        assert board.update([item], titles, {"notok": {"gc1"}})[0]["id"] == first[0]["id"]
        # a poll with nothing read (snapshot gone) is not a recovery
        clock[0] += 1
        assert board.update([], {}, None) == []
        clock[0] += 60
        after = board.update([], titles, {"notok": set()})
        assert [n["message"] for n in after] == ["GC-1 is OK to run again."]
        assert after[0]["level"] == "success" and after[0]["href"] == "/floor"
        clock[0] += ui_live.RECOVERED_SECONDS + 1
        assert board.update([], titles, {"notok": set()}) == []
        # it comes back: a new item, so a browser that dismissed the first sees it
        again = board.update([item], titles, {"notok": {"gc1"}})
        assert again[0]["id"] != first[0]["id"]

    def test_same_reason_not_ok_merges_and_a_new_member_is_a_new_item(self):
        def ms(uids):
            return [{"machine_uid": u, "title": u.upper(), "module_state": "running", "module_running": True,
                     "effective_specs": [{"test_name": "IBP", "last_qc_in_spec": False}],
                     "maintenance": []} for u in uids]
        def items(uids):
            m = ms(uids)
            ready = {x["machine_uid"]: ui_live.readiness(x, "") for x in m}
            return ui_live.conditions(machines=m, ready=ready, overrides={}, round_=None,
                                      audit_spool=0, live_road=None, certificates=None,
                                      href=lambda u, s: "/floor", now=datetime(2026, 10, 1, 10))
        two = items(["a", "b"])
        assert [i["message"] for i in two] == ["A and B are not OK to run: QC out of spec: IBP."], \
            "an acronym keeps its capitals ('qC out of spec' was the old lower-casing)"
        three = items(["a", "b", "c"])
        assert three[0]["key"] != two[0]["key"]
        one = items(["a"])
        assert one[0]["message"] == "A is not OK to run: QC out of spec: IBP."

    def test_overdue_calibrations_are_one_bell_item(self):
        """§5 lists "a calibration is overdue" as a bell item. It used to ride
        inside "not OK to run"; since calibration is a warning (Ryan,
        2026-10-01) it needs its own line, merged across instruments."""
        m = [{"machine_uid": u, "title": u.upper(), "module_state": "running", "module_running": True,
              "effective_specs": [{"test_name": "IBP", "last_qc_in_spec": True}],
              "maintenance": [{"kind": "calibration", "status": "RED"}]} for u in ("a", "b")]
        ready = {x["machine_uid"]: ui_live.readiness(x, "") for x in m}
        items = ui_live.conditions(machines=m, ready=ready, overrides={}, round_=None,
                                   audit_spool=0, live_road=None, certificates=None,
                                   href=lambda u, s: "/i/%s#%s" % (u, s), now=datetime(2026, 10, 1, 10))
        assert [i["message"] for i in items] == ["2 instruments are overdue for calibration: A and B."]
        assert items[0]["href"] == "/?cause=ok_but-cal"

    def test_same_cause_merges(self):
        ms = [{"machine_uid": u, "title": t, "module_state": "running", "module_running": True,
               "effective_specs": [{"test_name": "Flash", "last_qc_in_spec": None}]}
              for u, t in (("a", "OptiMPP 1"), ("b", "OptiMPP 2"))]
        ready = {m["machine_uid"]: ui_live.readiness(m, "") for m in ms}
        items = ui_live.conditions(machines=ms, ready=ready, overrides={}, round_=None,
                                   audit_spool=0, live_road=None, certificates=None,
                                   href=lambda u, s: "/floor", now=datetime(2026, 10, 1, 10))
        assert [i["message"] for i in items] == [
            "2 instruments are due for QC: OptiMPP 1 and OptiMPP 2."]


# ── readiness (§3.1) ────────────────────────────────────────────────────────

class TestReadiness:
    BASE = {"machine_uid": "u", "title": "GC", "status": "GREEN", "module_state": "running",
            "module_running": True, "effective_specs": [{"test_name": "IBP", "last_qc_in_spec": True}],
            "qc_targets": [{"test": "IBP"}], "maintenance": []}

    def _r(self, override="", **kw):
        return ui_live.readiness(dict(self.BASE, **kw), override)["state"]

    def test_each_row_of_the_table(self):
        assert self._r() == ui_live.OK
        assert self._r("SERVICE") == ui_live.OFF_LINE
        assert self._r(status="SERVICE") == ui_live.OFF_LINE
        assert self._r(effective_specs=[{"test_name": "IBP", "last_qc_in_spec": False}]) == ui_live.NOT_OK
        assert self._r(maintenance=[{"kind": "calibration", "status": "RED"}]) == ui_live.OK_BUT
        assert self._r(effective_specs=[{"test_name": "IBP", "last_qc_in_spec": None}]) == ui_live.OK_BUT
        assert self._r(maintenance=[{"kind": "pm", "status": "RED"}]) == ui_live.OK_BUT
        assert self._r(module_running=False, module_state="stopped") == ui_live.CANT_TELL
        assert self._r(effective_specs=[], qc_targets=[]) == ui_live.NO_QC

    def test_only_qc_or_an_override_can_say_no(self):
        """Ryan, 2026-10-01: "PM overdue = warning, Calibration overdue =
        WARNING too (not a stop). Only QC (and an explicit override / out of
        service) can make the answer No." The spec had calibration as a stop;
        his decision overrides it. An overdue calibration is a paperwork date,
        and the QC check run against the certificate band is what says
        whether the instrument still reads true."""
        cal = ui_live.readiness(dict(self.BASE, maintenance=[{"kind": "calibration", "status": "RED"}]), "")
        assert cal == {"state": ui_live.OK_BUT, "reason": "Calibration overdue"}
        both = ui_live.readiness(dict(self.BASE, maintenance=[{"kind": "calibration", "status": "RED"},
                                                              {"kind": "pm", "status": "RED"}]), "")
        assert both["reason"] == "Calibration overdue", "the calibration is the bigger of the two"
        failed = ui_live.readiness(dict(self.BASE, maintenance=[{"kind": "calibration", "status": "RED"}],
                                        effective_specs=[{"test_name": "IBP", "last_qc_in_spec": False}]), "")
        assert failed["state"] == ui_live.NOT_OK, "QC out of spec still says No"

    def test_a_superseded_failure_is_not_a_failure(self):
        """A failure against the OLD standard says nothing about the new one,
        so it is not a stop. Nor is it a pass: the new standard has not been
        run, which is QC due, the word the record's row says for it (piece 5,
        round 2: one rule for the card and every row)."""
        r = ui_live.readiness(dict(self.BASE, effective_specs=[
            {"test_name": "IBP", "sample_id": "AF27", "last_qc_in_spec": False,
             "last_qc_superseded_by": "AF26"}]), "")
        assert r == {"state": ui_live.OK_BUT, "reason": "QC due: IBP"}
        (c,) = ui_live.qc_checks(dict(self.BASE, effective_specs=[
            {"test_name": "IBP", "sample_id": "AF27", "last_qc_in_spec": False,
             "last_qc_superseded_by": "AF26"}]))
        assert c["verdict"]["detail"] == "not yet run against AF27"

    def test_dead_line_from_silence_is_not_off_line(self):
        """A bench says DEAD-LINE when no data arrives. That is not a decision
        anybody made, so it must not read as "Off line" (which is)."""
        assert self._r(status="DEAD-LINE", module_running=False,
                       module_state="stopped") == ui_live.CANT_TELL


# ── jobs and the log copy ───────────────────────────────────────────────────

class TestRunningNow:
    def test_a_job_shows_while_it_runs_and_for_30_min_after(self, tmp_path):
        app, _gw = _seeded(tmp_path)
        clock = [time.time()]
        import jobs
        app.config["JOBS"] = jobs.Registry(clock=lambda: clock[0])
        c = app.test_client()
        job = app.config["JOBS"].start("export", "Exporting QC", by="ryan")
        job.progress(40, 100, "Writing rows")
        got = c.get("/api/ui/live").get_json()["jobs"]
        assert [(j["title"], j["state"], j["progress"]) for j in got] == [
            ("Exporting QC", "running", {"done": 40, "total": 100, "text": "Writing rows"})]
        job.finish("Exported 1,204 rows")
        clock[0] += 29 * 60
        assert c.get("/api/ui/live").get_json()["jobs"][0]["outcome"] == "Exported 1,204 rows"
        clock[0] += 2 * 60
        assert c.get("/api/ui/live").get_json()["jobs"] == []

    def test_on_the_store_there_is_no_copy_to_be_behind(self, tmp_path):
        """On LEM's store the History reads the record itself
        (`StoreLogMirror`), so "the log copy" is always complete to now: there
        is no first fill to show in Running now and no refresh to fall behind.
        Telling a supervisor "the log copy is filling" about a copy that does
        not exist would be a banner about nothing. And still 0 store reads:
        the live feed answers from memory on every poll of every tab."""
        from log_mirror import StoreLogMirror
        app, gw = _seeded(tmp_path)
        mirror = app.config["LOG_MIRROR"]
        assert isinstance(mirror, StoreLogMirror)
        c = app.test_client()
        gw.calls.clear()
        m = c.get("/api/ui/live").get_json()["mirror"]
        assert m["state"] == "filled" and m["complete_to"]
        assert gw.calls == []
        assert not [j for j in c.get("/api/ui/live").get_json()["jobs"]
                    if j.get("kind") == "log-copy"]

    def test_the_log_copy_says_what_it_is_from_memory(self, tmp_path):
        """The COPY (`LogMirror`) still exists for a server whose record is
        in LabCore; its states are what the banner says about it."""
        from log_mirror import LogMirror
        app, gw = _seeded(tmp_path)
        mirror = LogMirror(gw, path=str(tmp_path / "copy.sqlite3"),
                           jobs=app.config["JOBS"])
        app.config["LOG_MIRROR"] = mirror
        c = app.test_client()
        assert c.get("/api/ui/live").get_json()["mirror"]["state"] == "empty"
        mirror.refresh()
        m = c.get("/api/ui/live").get_json()["mirror"]
        assert m["state"] == "filled" and m["complete_to"] and m["rows"] == mirror.count()
        jobs_now = c.get("/api/ui/live").get_json()["jobs"]
        assert jobs_now and jobs_now[0]["outcome"].startswith("Log copy filled")
        # a failed refresh after a fill is "behind", never "empty"
        orig = gw.read_sql
        gw.read_sql = lambda *a, **k: {"error": "database is locked"}
        with pytest.raises(Exception):
            mirror.refresh()
        gw.read_sql = orig
        m = c.get("/api/ui/live").get_json()["mirror"]
        assert m["state"] == "behind" and "locked" in m["reason"] and m["rows"] > 0


# ── one rule on both sides, and no timer POSTs ──────────────────────────────

class TestOneRuleBothSides:
    def test_the_server_says_the_nav_the_browser_says(self):
        """The first paint (ui_live.nav_meta) and the browser (status.js
        navMeta) run over the SAME cases file; tests/js/status.mjs reads it
        too. Two copies of a rule drift; one file of cases keeps them honest."""
        import json
        from pathlib import Path
        cases = json.loads((Path(__file__).parent / "fixtures" / "nav_meta_cases.json")
                           .read_text(encoding="utf-8"))
        for c in cases:
            assert ui_live.nav_meta(c["payload"]) == c["nav"], c["why"]


class TestNoTimerPosts:
    """Open pages must never POST on a timer (A.7): one would pin
    /healthz idle_seconds near zero and no release would deploy unattended.
    The live client is GETs only by construction (bgFetch refuses anything
    else, tests/js/live_poller.mjs); these check the shell's other live
    files do not send anything at all, and that every shell page loads them."""
    LIVE_JS = ("live.js", "status.js", "running_now.js", "notifications_panel.js")

    def test_the_live_files_never_post(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "static" / "js"
        for name in self.LIVE_JS:
            src = (root / name).read_text(encoding="utf-8")
            import re
            # a POST is a string literal in code ('POST' / "POST"); prose
            # in comments that explains why there are none does not count
            assert not re.search(r"""['"]POST['"]""", src), name
            assert "sendBeacon" not in src and "XMLHttpRequest" not in src, name
            if name != "live.js":
                assert "fetch(" not in src, name         # only the poller asks anything

    def test_every_shell_page_loads_the_live_client_once(self, tmp_path):
        app, _gw = _seeded(tmp_path)
        c = _signed_in(app)
        for path in ("/settings", "/help"):
            page = c.get(path).get_data(as_text=True)
            for name in self.LIVE_JS:
                assert page.count("/static/js/%s?v=" % name) == 1, (path, name)
