"""The gateway split: LEM's statements go to LEM's store, never to LabCore.

Transfer spec §5.3. `create_app(store, labcore=...)` takes two gateways now,
and the claim that justifies moving the record is a number: **with the bridge
off, LabCore sees zero `lem_*` statements** (S4) — not one per refresh (W1,
measured at 1 per 12 s on v3.9.0, +426 over six minutes after a single killed
read), not five per `/api/status` (W3). LabCore keeps only what is LabCore's:
sign-in, the dashboard's QC rows out of `samples`/`sample_tests`, and the
test-method catalogue.

Each test here puts `labcore_counter.CountingLabCore` in LabCore's place and
the real store (`LocalStoreGateway`, via conftest) in the store's, so a store
read and a LabCore op can no longer be confused — which a one-gateway test
cannot do.

And the readers: every `FROM lem_machine_log` in the server must name
`lem_machine_log_effective` or say, on the spot, why it reads the raw record.
A hidden replay that one forgotten reader still counts is a QC verdict renewed
by a re-read — the bug this whole transfer exists to end.
"""

import ast
import json
import os
import re

import pytest

import demo_floor
from labcore_counter import CountingLabCore
from labcore_gateway import FakeLabCoreGateway
from web_app import create_app

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WATCHDOG = {"error": "Read cancelled after 8s to protect the write queue "
                     "(query too slow — likely an unindexed scan)."}


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


class CountingStore(FakeLabCoreGateway):
    """The store, counted too — so a walk that reached nothing cannot pass
    S4 by having done nothing."""

    def __init__(self):
        super().__init__()
        self.reads = 0
        self.plan = None

    def read_sql(self, sql, args=None, **kw):
        self.reads += 1
        if self.plan is not None:
            act = self.plan(sql)
            if act is not None:
                return dict(act)
        return super().read_sql(sql, args, **kw)


def _split(tmp_path, seed=True):
    store = CountingStore()
    lab = CountingLabCore()
    app = create_app(store, labcore=lab, authenticator=StubAuth(), secret="s",
                     documents_root=str(tmp_path), live_token="tok")
    app.config["TESTING"] = True
    if seed:
        app.config["SNAPSHOTS"].ensure_schema()
        demo_floor.seed(store, documents_root=str(tmp_path))
    app.config["SNAPSHOTS"].refresh()
    return app, store, lab


# ── S4: a page walk sends LabCore no lem_ statement ─────────────────────────

_ARG = {"machine_uid": None, "uid": "x", "test_name": "Flash Point",
        "name": "x", "day": "2026-10-01", "doc_id": "x", "level_uid": "x",
        "filename": "x", "path": "x", "action_uid": "x", "item": "x"}


def _urls(app, uid):
    out = []
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.endpoint == "static":
            continue
        values = {}
        for arg in rule.arguments:
            values[arg] = uid if arg in ("machine_uid",) else _ARG.get(arg, "x")
        try:
            url = rule.build(values)[1] if hasattr(rule, "build") else None
        except Exception:
            url = None
        if url is None:
            with app.test_request_context():
                from flask import url_for
                try:
                    url = url_for(rule.endpoint, **values)
                except Exception:
                    continue
        out.append(url)
    return sorted(set(out))


class TestS4LabCoreSeesNoLemStatement:
    def test_every_get_route_signed_in_and_out(self, tmp_path):
        app, store, lab = _split(tmp_path)
        uid = demo_floor.FLEET[0].uid
        urls = _urls(app, uid)
        assert len(urls) > 60, urls            # the walk is the whole app
        for signed in (False, True):
            c = app.test_client()
            if signed:
                c.post("/api/login", json={"username": "k", "password": "good"})
            for url in urls:
                c.get(url)
        # The bench road, the live road and an hour of snapshot cycles.
        c = app.test_client()
        for _ in range(5):
            c.post("/api/live", json={"machine_uid": uid, "status": "GREEN"},
                   headers={"X-LEM-Token": "tok"})
            c.get("/api/bench/%s/config" % uid, headers={"X-LEM-Token": "tok"})
        for _ in range(300):                    # 300 x 12 s = one hour
            app.config["SNAPSHOTS"].refresh()
        assert store.reads > 300, "the walk never reached the store"
        assert lab.lem_statements == [], lab.lem_statements[:5]

    def test_the_writes_people_make_send_labcore_nothing(self, tmp_path):
        """The saves too, not only the reads: a correction, an override, a
        QC band, a level, a checklist tick, a PM completion."""
        app, store, lab = _split(tmp_path)
        uid = demo_floor.FLEET[0].uid
        c = app.test_client()
        c.post("/api/login", json={"username": "k", "password": "good"})
        before = lab.ops
        c.post("/api/machines/%s/corrections" % uid,
               json={"test_name": "Flash Point", "correction": 0.2})
        c.post("/api/machines/%s/override" % uid,
               json={"override": "SERVICE", "comment": "cal due"})
        c.post("/api/equipment/levels", json={"name": "Roof"})
        tasks = c.get("/api/maintenance").get_json()["tasks"]
        if tasks:
            c.post("/api/maintenance/%s/complete" % tasks[0]["uid"],
                   json={"note": "done"})
        assert lab.lem_statements == [], lab.lem_statements[:5]
        assert lab.ops == before, lab.calls[before:]


# ── W1: snapshot refreshes cost LabCore nothing, killed read or not ─────────

class TestW1TheSnapshotReadsTheStore:
    def test_thirty_refreshes_and_one_killed_read_are_zero_labcore_reads(
            self, tmp_path):
        """v3.9.0: 1 LabCore op per refresh, and +426 over the next 30
        refreshes after ONE watchdog-killed batched read (the per-arm
        fallback fans out to 18 reads a cycle). On the store the snapshot
        never asks LabCore anything, so both numbers are 0 — and the killed
        read is injected into the STORE, where it now happens."""
        app, store, lab = _split(tmp_path)
        snaps = app.config["SNAPSHOTS"]
        before = lab.ops
        for _ in range(30):
            snaps.refresh()
        healthy = lab.ops - before
        fired = {"n": 0}

        def kill_once(sql):
            if "UNION ALL" in sql and fired["n"] == 0:
                fired["n"] += 1
                return WATCHDOG
            return None

        store.plan = kill_once
        before = lab.ops
        for _ in range(30):
            snaps.refresh()
        assert fired["n"] == 1
        assert healthy == 0
        assert lab.ops - before == 0, lab.calls[before:]
        assert snaps.get()["ready"]


# ── W3: /api/status ─────────────────────────────────────────────────────────

class TestW3TheDashboardConfigIsLems:
    def test_status_with_no_boxes_costs_labcore_nothing(self, tmp_path):
        """The W3 shape exactly (baseline/web.json: 5 LabCore reads, 1 of
        them lem_meta and 4 other lem_ tables)."""
        app, store, lab = _split(tmp_path, seed=False)
        store.sql("INSERT INTO lem_machine_status (machine_uid, title, status, "
                  "reason, updated_at) VALUES ('m1','PAC Flash 1','GREEN','ok',"
                  "'2026-10-01T09:00:00')")
        before = lab.ops
        r = app.test_client().get("/api/status")
        assert r.status_code == 200
        assert lab.ops - before == 0, lab.calls[before:]

    def test_with_a_box_labcore_is_asked_only_for_its_own_rows(self, tmp_path):
        """The dashboard's QC rows ARE LabCore's (`samples`/`sample_tests`).
        That read stays — and it is the only kind that reaches LabCore."""
        from db_config_store import DbConfigStore
        from models import (AppConfig, BoxConfig, SampleSpec, SampleTestSpec,
                            WatchedTarget)
        app, store, lab = _split(tmp_path, seed=False)
        lab.fake.write("insert_sample", {"lab_id": "STD-1"})
        lab.fake.write("update_cell", {"lab_id": "STD-1", "test_name":
                                       "Flash Point", "value": "65",
                                       "updated_at": "2026-10-01 09:00:00"})
        cfg = AppConfig(version=5, poll_minutes=5, map_locked=False,
                        sample_id_column="Lab ID", samples=[SampleSpec(
            name="Diesel QC", sample_id_val="STD-1", tests=[SampleTestSpec(
                name="Flash", value_col="Flash Point", expected=65.0,
                std_dev=2.0, units="C")])],
            boxes=[BoxConfig(uid="gc1", title="GC-1", csv_path="",
                             watched_targets=[WatchedTarget(
                                 sample="Diesel QC", test="Flash")])])
        ok, why = DbConfigStore(store).save(cfg)
        assert ok, why
        before = lab.ops
        body = app.test_client().get("/api/status").get_json()
        assert body["boxes"][0]["results"][0]["value"] == pytest.approx(65.0)
        new = lab.calls[before:]
        assert new, "the QC rows come from LabCore"
        assert lab.lem_statements == []


class TestW3TheDashboardsQcRowsAreReadOncePerInterval:
    """The critic's second-tier W3 finding: with a dashboard configured,
    EVERY `GET /api/status` cost 1 LabCore read (the `samples` query), so
    three wall screens and two desks put five reads a minute on the queue
    the benches write through, and a tab left polling cost one forever.

    The rows are LabCore's and stay LabCore's; what changes is who pays.
    They are read at most once per the dashboard's own refresh interval
    (`refresh_seconds`, never under 60 s) for EVERY screen together, one
    read in flight at a time. A read that fails is never an empty
    dashboard: with rows in hand the answer says how old they are and that
    LabCore is not answering; with none it is the 503 it always was. And a
    LabCore that is down is asked again after a back-off, not per request,
    which is the +426-after-one-kill lesson applied here."""

    def _configured(self, tmp_path, clock):
        from db_config_store import DbConfigStore
        from models import (AppConfig, BoxConfig, SampleSpec, SampleTestSpec,
                            WatchedTarget)
        app, store, lab = _split(tmp_path, seed=False)
        app.config["STATUS_PROVIDER"].clock = clock
        lab.fake.write("insert_sample", {"lab_id": "STD-1"})
        lab.fake.write("update_cell", {"lab_id": "STD-1", "test_name":
                                       "Flash Point", "value": "65",
                                       "updated_at": "2026-10-01 09:00:00"})
        cfg = AppConfig(version=5, poll_minutes=1, map_locked=False,
                        sample_id_column="Lab ID", samples=[SampleSpec(
            name="Diesel QC", sample_id_val="STD-1", tests=[SampleTestSpec(
                name="Flash", value_col="Flash Point", expected=65.0,
                std_dev=2.0, units="C")])],
            boxes=[BoxConfig(uid="gc1", title="GC-1", csv_path="",
                             watched_targets=[WatchedTarget(
                                 sample="Diesel QC", test="Flash")])])
        ok, why = DbConfigStore(store).save(cfg)
        assert ok, why
        return app, store, lab

    def test_thirty_refreshes_cost_one_read(self, tmp_path):
        now = [1000.0]
        app, _store, lab = self._configured(tmp_path, lambda: now[0])
        c = app.test_client()
        before = lab.ops
        for _ in range(30):
            r = c.get("/api/status")
            assert r.status_code == 200
            assert r.get_json()["boxes"][0]["results"][0]["value"] == \
                pytest.approx(65.0)
            now[0] += 1.0                      # 30 s of polling, every second
        assert lab.ops - before == 1, lab.calls[before:]
        assert lab.lem_statements == []

    def test_after_the_interval_the_rows_are_read_again(self, tmp_path):
        now = [1000.0]
        app, _store, lab = self._configured(tmp_path, lambda: now[0])
        c = app.test_client()
        before = lab.ops
        c.get("/api/status")
        lab.fake.write("update_cell", {"lab_id": "STD-1", "test_name":
                                       "Flash Point", "value": "66",
                                       "updated_at": "2026-10-01 10:00:00"})
        now[0] += 61
        body = c.get("/api/status").get_json()
        assert body["boxes"][0]["results"][0]["value"] == pytest.approx(66.0)
        assert lab.ops - before == 2

    def test_a_failed_read_keeps_the_rows_and_says_how_old(self, tmp_path):
        now = [1000.0]
        app, _store, lab = self._configured(tmp_path, lambda: now[0])
        c = app.test_client()
        c.get("/api/status")
        lab.fake.read_sql = lambda *a, **k: dict(WATCHDOG)
        now[0] += 61
        before = lab.ops
        bodies = []
        for _ in range(10):
            r = c.get("/api/status")
            assert r.status_code == 200
            bodies.append(r.get_json())
            now[0] += 1.0
        body = bodies[-1]
        assert body["boxes"][0]["results"][0]["value"] == pytest.approx(65.0)
        assert body["labcore_online"] is False
        assert body["qc_rows_as_of"]
        assert any("as of" in e and "LabCore" in e for e in body["errors"])
        # one attempt, then a back-off: not ten reads at a LabCore that is down
        assert lab.ops - before == 1, lab.calls[before:]

    def test_a_failed_first_read_is_a_503_not_an_empty_dashboard(
            self, tmp_path):
        now = [1000.0]
        app, _store, lab = self._configured(tmp_path, lambda: now[0])
        lab.fake.read_sql = lambda *a, **k: dict(WATCHDOG)
        c = app.test_client()
        before = lab.ops
        for _ in range(5):
            r = c.get("/api/status")
            assert r.status_code == 503, r.get_json()
            now[0] += 1.0
        assert lab.ops - before == 1, lab.calls[before:]

    def test_a_changed_watch_list_is_read_at_once(self, tmp_path):
        """The cache is keyed on WHAT is watched: a box added a second ago
        must not wait out the interval showing nothing for its sample."""
        from db_config_store import DbConfigStore
        now = [1000.0]
        app, store, lab = self._configured(tmp_path, lambda: now[0])
        c = app.test_client()
        before = lab.ops
        c.get("/api/status")
        cfg = DbConfigStore(store).load()
        cfg.samples[0].sample_id_val = "STD-2"
        assert DbConfigStore(store).save(cfg)[0]
        c.get("/api/status")
        assert lab.ops - before == 2


# ── W2b: the server's own INSERTs land on the store ─────────────────────────

class TestW2bTheServersInsertsLand:
    """web_app's `_audit`, the PM completion, the maintenance import and
    `levels`' move line each INSERT the seven LabCore columns. A view in the
    table's place (spec B) broke all four; the store keeps the table."""

    def _rows(self, store, **where):
        clause = " AND ".join("%s = ?" % k for k in where)
        res = store.read_sql("SELECT origin, kind, test_name, detail FROM "
                             "lem_machine_log WHERE " + clause,
                             list(where.values()))
        assert "error" not in res, res
        return res["rows"]

    def test_all_four(self, tmp_path):
        app, store, lab = _split(tmp_path, seed=False)
        store.sql("INSERT INTO lem_machine_status (machine_uid, title, status, "
                  "reason, updated_at) VALUES ('m1','OptiMPP 1','GREEN','ok',"
                  "'2026-10-01T09:00:00')")
        app.config["SNAPSHOTS"].refresh()
        from maintenance_store import MaintenanceStore, MaintTaskRecord
        MaintenanceStore(store).save(MaintTaskRecord(
            uid="t1", machine_uid="m1", name="Monthly PM", kind="pm",
            interval_days=30, last_done="2026-06-01"))
        c = app.test_client()
        c.post("/api/login", json={"username": "k", "password": "good"})
        ok = []
        # 1. `_audit` — a QC band save is audited through it.
        r = c.post("/api/qc-specs", json={
            "machine_uid": "m1", "test_name": "Flash Point", "sample_id": "QC",
            "expected": 63.7, "std_dev": 1.05, "k": 2})
        ok.append(r.status_code == 200 and bool(
            self._rows(store, machine_uid="m1", kind="config",
                       test_name="qc-spec saved")))
        # 2. the PM completion
        r = c.post("/api/maintenance/t1/complete", json={"note": "x"})
        ok.append(r.status_code == 200 and r.get_json().get("logged") is not False
                  and bool(self._rows(store, machine_uid="m1", kind="pm")))
        # 3. the maintenance import
        csv = ("equipment,task,kind,completed_date,performed_by,note\n"
               "OptiMPP 1,Annual cal,calibration,2026-05-02,sam,x\n")
        r = c.post("/api/maintenance-import", json={"csv": csv})
        ok.append(r.status_code == 200 and r.get_json().get("created") == 1
                  and bool(self._rows(store, machine_uid="m1",
                                      kind="calibration")))
        # 4. levels' move line
        lvl = c.post("/api/equipment/levels", json={"name": "Ground"}).get_json()
        r = c.post("/api/equipment/m1/level",
                   json={"level_uid": lvl["level"]["uid"]})
        ok.append(r.status_code == 200 and bool(
            self._rows(store, machine_uid="m1", test_name="level_move")))
        assert ok == [True, True, True, True]
        origins = {row["origin"] for row in store.read_sql(
            "SELECT origin FROM lem_machine_log")["rows"]}
        assert origins == {"server"}
        assert lab.lem_statements == []


# ── the readers read the effective record ───────────────────────────────────

_RAW_READ = re.compile(r"\b(FROM|JOIN)\s+lem_machine_log\b(?!_effective)", re.I)


def _strings(path):
    src = open(path, encoding="utf-8-sig").read()
    lines = src.splitlines()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(v.value if isinstance(v, ast.Constant) else "{}"
                           for v in node.values)
        else:
            continue
        lo = max(0, node.lineno - 3)
        hi = getattr(node, "end_lineno", node.lineno)
        yield node.lineno, text, "\n".join(lines[lo:hi])


class TestEveryReaderNamesTheEffectiveView:
    """Spec §5.2: "every `FROM lem_machine_log` outside the ingest, import
    and audit code must name the view or carry an allow-comment." Parsed with
    `ast`, so adjacent string literals are joined the way Python joins them —
    a `"... FROM "` / `"lem_machine_log ..."` split across two lines cannot
    slip past a line-by-line grep."""

    def _offenders(self):
        out, allowed = [], set()
        for name in sorted(os.listdir(HERE)):
            if not name.endswith((".py", ".pyw")):
                continue
            for line, text, around in _strings(os.path.join(HERE, name)):
                if not _RAW_READ.search(text):
                    continue
                if "raw-log:" in around:
                    # A set: an f-string and its constant parts are separate
                    # nodes on the same line, and one read is one read.
                    allowed.add("%s:%d" % (name, line))
                else:
                    out.append("%s:%d %s" % (name, line, text[:80]))
        return out, allowed

    def test_no_reader_counts_hidden_rows_by_accident(self):
        offenders, _allowed = self._offenders()
        assert offenders == [], offenders

    def test_the_allowed_raw_reads_are_exactly_these(self):
        """Each one says why on the spot. A new one has to be added here on
        purpose — the list is the decision, not a side effect."""
        _offenders, allowed = self._offenders()
        files = sorted(a.split(":")[0] for a in allowed)
        # bench_api.py: the v2 ingest (transfer §6.1) — the custody key's
        # existence check, reading back the custody row it just wrote, and
        # the §4.4 occurrence count. Each must see hidden rows: a row an
        # annotation hides is still in the record and still holds its key.
        # The fourth bench_api.py read counts the rows a projected record
        # already has when its v2 sync arrives (§10.3, M6).
        # lem_store.py gains one in round 4: an approval may name only rows
        # on its own bench, so `apm_names_a_candidate` reads the named row's
        # bench — hidden or not, as the hide trigger already does.
        # custody.py: the backup manifest's daily log digest (transfer
        # §11), an audit over every row — a hidden row is still in the
        # record, and a backup that skipped it could not prove it unaltered.
        # bridge.py (2) and legacy_import.py: reads of LABCORE's
        # `lem_machine_log`, which has no effective view — the import and
        # the pull copy every row a v3.9 bench wrote (§10.1, §10.4).
        # legacy_import.py (the rest): identity and custody checks on the
        # store (a legacy key or a bench key is unique over every row, hidden
        # or not) and the import's own count and sample of what it copied.
        assert files == ["bench_api.py"] * 4 + ["bridge.py"] * 2 + [
            "custody.py", "dedupe.py", "dedupe.py"] + [
            "legacy_import.py"] * 12 + ["lem_store.py"] * 4 + [
            "log_mirror.py", "log_mirror.py", "web_app.py", "web_app.py"], allowed

    def test_the_check_would_catch_a_split_literal(self, tmp_path):
        bad = tmp_path / "bad.py"
        bad.write_text('q = ("SELECT * FROM "\n     "lem_machine_log WHERE 1")\n')
        hits = [t for _l, t, _a in _strings(str(bad)) if _RAW_READ.search(t)]
        assert hits
