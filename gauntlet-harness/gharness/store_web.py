"""W1, W2, W3 and W2b on a server that has a LEM store (transfer §5, P6).

Phase 1's `run_web.py` measures a server whose only database is LabCore:
every op it counts is a LabCore op because there is nothing else to count.
On v4 that is no longer true — the server reads and writes its own store — so
these are the same four questions asked of the split server:

    create_app(LocalStoreGateway(<fresh store under the gate's tmp root>),
               labcore=<run_web.WGateway, counting every LabCore op>)

and the numbers reported are LabCore's, which is what W1/W3 were always about
("LabCore reads per refresh", "what one /api/status costs LabCore"). The
store's own work is reported beside them (`store_*`) so a run that did nothing
cannot pass by doing nothing.

W2's "raise after commit" is a response lost AFTER the transaction committed:
an `after_request` hook raises once on the save, so the browser gets a 500,
then the same request is sent again with the same `X-Request-Id`.
"""
import itertools
import json
import os

_serial = itertools.count()

WATCHDOG = {"error": "Read cancelled after 8s to protect the write queue "
                     "(query too slow — likely an unindexed scan)."}


def _store_path(tag):
    base = os.path.dirname(os.environ["LEM_STORE_PATH"])
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "web-%s-%03d.sqlite3" % (tag, next(_serial)))


def _make(rw, tag, *, before_first_request=None):
    import web_app
    from lem_store import LocalStoreGateway

    class CountedStore(LocalStoreGateway):
        """The store, with a one-shot fault plan like WGateway's."""
        plan = None
        reads = 0

        def read_sql(self, sql, args=None, **kw):
            type(self).reads += 1
            if self.plan is not None:
                act = self.plan(sql)
                if act is not None:
                    return dict(act)
            return super().read_sql(sql, args, **kw)

    CountedStore.reads = 0
    store = CountedStore(_store_path(tag))
    store.sql("CREATE TABLE IF NOT EXISTS lem_machine_status (machine_uid TEXT "
              "PRIMARY KEY, title TEXT, status TEXT, reason TEXT, updated_at "
              "TEXT)")
    store.sql("INSERT INTO lem_machine_status (machine_uid, title, status, "
              "reason, updated_at) VALUES ('m1','PAC Flash 1','GREEN','ok',"
              "'2026-10-01T09:00:00')")
    class Lab(rw.WGateway):
        """WGateway, also counting statements that name a lem_ table (S4)."""
        lem = 0

        def read_sql(self, sql, args=None, **kw):
            if "LEM_" in str(sql).upper():
                type(self).lem += 1
            return super().read_sql(sql, args, **kw)

        def sql(self, sql, args=None, source="", **kw):
            if "LEM_" in str(sql).upper():
                type(self).lem += 1
            return super().sql(sql, args, source=source, **kw)

    Lab.lem = 0
    lab = Lab()
    app = web_app.create_app(store, labcore=lab, authenticator=rw.StubAuth(),
                             secret="s")
    app.config["TESTING"] = True
    if before_first_request is not None:
        before_first_request(app)
    return store, lab, app


def _ops(lab):
    return sum(lab.counts.values())


def w1(rw):
    store, lab, app = _make(rw, "w1")
    snaps = app.config["SNAPSHOTS"]
    snaps.refresh()
    boot = _ops(lab)
    c0 = _ops(lab)
    s0 = type(store).reads
    for _ in range(30):
        snaps.refresh()
    healthy = (_ops(lab) - c0) / 30.0
    store_per = (type(store).reads - s0) / 30.0
    fired = {"n": 0}

    def kill_once(sql):
        if "UNION ALL" in sql and fired["n"] == 0:
            fired["n"] += 1
            return WATCHDOG
        return None

    store.plan = kill_once
    per = []
    for _ in range(30):
        a = _ops(lab)
        snaps.refresh()
        per.append(_ops(lab) - a)
    store.plan = None
    snap = snaps.get()
    return {"boot_ops": boot, "ops_per_refresh_healthy": healthy,
            "store_reads_per_refresh_healthy": store_per,
            "after_one_watchdog": {
                "injected_into": "store", "fired": fired["n"],
                "ops_next_30_refreshes": sum(per), "per_refresh": per,
                "extra_ops_vs_healthy": sum(per) - healthy * 30,
                "stale_after": snap.get("stale"),
                "error_after": snap.get("error")},
            "lem_sql_to_labcore": type(lab).lem}


def w2(rw):
    from flask import request

    lost = {"n": 0}

    def lose_once(app):
        app.config["PROPAGATE_EXCEPTIONS"] = False

        @app.after_request
        def _lose(response):
            if lost["n"] == 0 and request.path.endswith("/corrections") \
                    and response.status_code == 200:
                lost["n"] += 1
                raise ConnectionResetError("harness: response lost after commit")
            return response

    store, lab, app = _make(rw, "w2", before_first_request=lose_once)
    cl = app.test_client()
    cl.post("/api/login", json={"username": "k", "password": "good"})

    def n(sql):
        r = store.read_sql(sql)
        if r.get("error"):
            return "READ FAILED: " + r["error"]
        return r["rows"][0]["n"]

    def rows():
        return {"correction_rows": n("SELECT COUNT(*) n FROM lem_correction_factors"),
                "audit_rows": n("SELECT COUNT(*) n FROM lem_correction_audit"),
                "config_log_rows": n(
                    "SELECT COUNT(*) n FROM lem_machine_log WHERE kind='config' "
                    "AND test_name='correction factor set'")}

    body = {"test_name": "Flash", "correction": 0.5, "units": "C"}
    hdr = {"X-Request-Id": "gate-w2-1"}
    l0 = _ops(lab)
    r1 = cl.post("/api/machines/m1/corrections", json=body, headers=hdr)
    after1 = rows()
    r2 = cl.post("/api/machines/m1/corrections", json=body, headers=hdr)
    return {"first_http": r1.status_code, "response_lost": lost["n"] == 1,
            "after_first": after1, "retry_http": r2.status_code,
            "retry_replayed": r2.headers.get("X-Request-Replayed") == "true",
            "after_retry": rows(), "labcore_ops": _ops(lab) - l0}


def w3(rw):
    from collections import Counter
    store, lab, app = _make(rw, "w3")
    app.config["SNAPSHOTS"].refresh()
    cl = app.test_client()
    c0 = Counter(lab.counts)
    s0 = type(store).reads
    r = cl.get("/api/status")
    d = Counter(lab.counts) - c0
    return {"http": r.status_code, "ops": sum(d.values()), "detail": dict(d),
            "store_reads": type(store).reads - s0,
            "lem_sql_to_labcore": type(lab).lem}


def w2b(rw):
    """The four server INSERTs into `lem_machine_log` (web_app's `_audit`,
    the PM completion, the maintenance import, levels' move line), each
    driven through its route, each confirmed landed on the store."""
    from maintenance_store import MaintenanceStore, MaintTaskRecord
    store, lab, app = _make(rw, "w2b")
    app.config["SNAPSHOTS"].refresh()
    MaintenanceStore(store).save(MaintTaskRecord(
        uid="t1", machine_uid="m1", name="Monthly PM", kind="pm",
        interval_days=30, last_done="2026-06-01"))
    cl = app.test_client()
    cl.post("/api/login", json={"username": "k", "password": "good"})

    def landed(**where):
        clause = " AND ".join("%s = ?" % k for k in where)
        r = store.read_sql("SELECT COUNT(*) n FROM lem_machine_log WHERE "
                           + clause, list(where.values()))
        return (not r.get("error")) and r["rows"][0]["n"] > 0

    out = {}
    r = cl.post("/api/qc-specs", json={
        "machine_uid": "m1", "test_name": "Flash Point", "sample_id": "QC",
        "expected": 63.7, "std_dev": 1.05, "k": 2})
    out["audit"] = r.status_code == 200 and landed(
        machine_uid="m1", kind="config", test_name="qc-spec saved")
    r = cl.post("/api/maintenance/t1/complete", json={"note": "x"})
    out["pm_completion"] = r.status_code == 200 and landed(
        machine_uid="m1", kind="pm")
    csv = ("equipment,task,kind,completed_date,performed_by,note\n"
           "PAC Flash 1,Annual cal,calibration,2026-05-02,sam,x\n")
    r = cl.post("/api/maintenance-import", json={"csv": csv})
    out["maintenance_import"] = r.status_code == 200 and landed(
        machine_uid="m1", kind="calibration")
    lvl = cl.post("/api/equipment/levels", json={"name": "Ground"}).get_json()
    r = cl.post("/api/equipment/m1/level",
                json={"level_uid": (lvl or {}).get("level", {}).get("uid", "")})
    out["level_move"] = r.status_code == 200 and landed(
        machine_uid="m1", test_name="level_move")
    origins = store.read_sql("SELECT DISTINCT origin FROM lem_machine_log")
    return {"store_inserts_ok": sum(1 for v in out.values() if v),
            "each": out,
            "origins": sorted(r["origin"] for r in origins.get("rows") or []),
            "lem_sql_to_labcore": type(lab).lem}


def w1_with_bridge(rw):
    """W1 on the store, plus the same question asked of the bridge (§9.3:
    'with the bridge on: 1 read per 12 s plus 1 per 60 s, and +0 after a
    kill'). Without a bridge in the target, `bridge_on` says so."""
    out = w1(rw)
    from . import mixed_fleet
    from .world import Unsupported
    try:
        out["bridge_on"] = mixed_fleet.w1_bridge_on(rw)
    except Unsupported as exc:
        out["bridge_on"] = {"unsupported": str(exc)}
    return out

