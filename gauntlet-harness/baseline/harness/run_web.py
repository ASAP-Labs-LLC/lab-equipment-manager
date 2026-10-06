"""Web-server side of the baseline, offline against FakeLabCoreGateway.

W1  LabCore ops per snapshot refresh, healthy and after ONE watchdog-killed
    read (LabCore's real error text), and after one busy refusal.
W2  A correction-factor save whose LabCore write lands but whose response is
    lost (client timeout) — what the operator sees, and what a retry leaves.
W3  What one GET /api/status costs (this session made one against
    production by mistake; this measures its price).
W4  LogMirror pull interrupted mid-fill, then resumed: rows lost/duplicated.
"""
import json
import os
import sys
import tempfile
from collections import Counter

sys.dont_write_bytecode = True
os.environ["LEM_DATA_DIR"] = tempfile.mkdtemp(prefix="lemdata-")
from lemharness import HGateway, categorize, BUSY  # noqa: E402  (sets sys.path)

from web_app import create_app  # noqa: E402
import snapshot_service  # noqa: E402

WATCHDOG = {"error": "Read cancelled after 8s to protect the write queue "
                     "(query too slow — likely an unindexed scan)."}


class WGateway(HGateway):
    """HGateway with the extra surface create_app touches."""

    def get_test_names(self, **kw):
        return self.fake.get_test_names()

    def get_samples(self, **kw):
        return self.fake.get_samples()

    def read_sql(self, sql, args=None, **kw):
        cat = categorize("read", sql)
        self.counts[cat] += 1
        act = self.plan("read", cat, sql) if self.plan else None
        if act == "watchdog":
            return dict(WATCHDOG)
        if act == "refuse":
            return dict(BUSY)
        if act == "raise_after":
            self.fake.read_sql(sql, args)
            raise TimeoutError("harness: response lost")
        return self.fake.read_sql(sql, args)

    def sql(self, sql, args=None, source="", **kw):
        cat = categorize("sql", sql)
        self.counts[cat] += 1
        act = self.plan("sql", cat, sql) if self.plan else None
        if act == "refuse":
            return dict(BUSY)
        res = self.fake.sql(sql, args)
        if act == "raise_after":
            raise TimeoutError("harness: response lost (write landed)")
        return res


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


def make():
    gw = WGateway()
    gw.fake.sql("INSERT INTO lem_machine_status (machine_uid, title, status, "
                "reason, updated_at) VALUES ('m1','PAC Flash 1','GREEN','ok',"
                "'2026-10-01T09:00:00')")
    app = create_app(gw, authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return gw, app


def w1():
    gw, app = make()
    snaps = app.config["SNAPSHOTS"]
    snaps.refresh()                      # boot: schema probe + first read
    boot = sum(gw.counts.values())
    c0 = sum(gw.counts.values())
    for _ in range(10):
        snaps.refresh()
    healthy = (sum(gw.counts.values()) - c0) / 10.0

    def once(action):
        fired = {"n": 0}

        def plan(kind, cat, payload):
            if kind == "read" and "UNION ALL" in str(payload) and fired["n"] == 0:
                fired["n"] += 1
                return action
            return None
        return plan

    out = {"boot_ops": boot, "ops_per_refresh_healthy": healthy,
           "arms": len(snapshot_service._ARMS),
           "BATCHED_RETRY_AFTER_refreshes": snapshot_service.BATCHED_RETRY_AFTER}
    for name, action in (("watchdog", "watchdog"), ("busy", "refuse")):
        gw.plan = once(action)
        c0 = sum(gw.counts.values())
        per = []
        for _ in range(30):
            a = sum(gw.counts.values())
            snaps.refresh()
            per.append(sum(gw.counts.values()) - a)
        gw.plan = None
        snap = snaps.get()
        out["after_one_" + name] = {
            "ops_next_30_refreshes": sum(gw.counts.values()) - c0,
            "per_refresh": per,
            "extra_ops_vs_healthy": sum(per) - healthy * 30,
            "minutes_at_12s": round(30 * 12 / 60.0, 1),
            "stale_after": snap.get("stale"), "error_after": snap.get("error")}
    return out


def w2():
    gw, app = make()
    cl = app.test_client()
    cl.post("/api/login", json={"username": "k", "password": "good"})

    def corr_rows():
        r = gw.fake.read_sql("SELECT COUNT(*) n FROM lem_correction_factors")
        if r.get("error"):
            return "READ FAILED: " + r["error"]
        return r["rows"][0]["n"]

    def audit_rows():
        r = gw.fake.read_sql("SELECT COUNT(*) n FROM lem_machine_log "
                             "WHERE detail LIKE '%correction%'")
        if r.get("error"):
            return "READ FAILED: " + r["error"]
        return r["rows"][0]["n"]

    first = {"n": 0}

    def plan(kind, cat, payload):
        if kind == "sql" and cat == "sql:corrections" and \
                str(payload).lstrip().upper().startswith("INSERT") and first["n"] == 0:
            first["n"] += 1
            return "raise_after"
        return None
    gw.plan = plan
    r1 = cl.post("/api/machines/m1/corrections",
                 json={"test_name": "Flash", "correction": 0.5, "units": "C"})
    gw.plan = None
    after1 = (corr_rows(), audit_rows())
    r2 = cl.post("/api/machines/m1/corrections",
                 json={"test_name": "Flash", "correction": 0.5, "units": "C"})
    after2 = (corr_rows(), audit_rows())
    return {"first_http": r1.status_code, "first_body": r1.get_json(),
            "after_first": {"correction_rows": after1[0], "audit_rows": after1[1]},
            "retry_http": r2.status_code,
            "after_retry": {"correction_rows": after2[0], "audit_rows": after2[1]},
            "audit_spool": cl.get("/healthz").get_json().get("audit_spool")}


def w3():
    gw, app = make()
    cl = app.test_client()
    app.config["SNAPSHOTS"].refresh()
    c0 = Counter(gw.counts)
    r = cl.get("/api/status")
    d = Counter(gw.counts) - c0
    return {"http": r.status_code, "ops": sum(d.values()), "detail": dict(d)}


def w4():
    import log_mirror
    gw, app = make()
    for i in range(250):
        gw.fake.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, lab_id, "
                    "test_name, value, detail) VALUES (?,?,?,?,?,?,?)",
                    ["m1", "2026-10-01T09:%02d:%02d" % (i // 60, i % 60), "run",
                     "L%d" % i, "", "", "{}"])
    path = os.path.join(tempfile.mkdtemp(), "mirror.sqlite3")
    mirror = log_mirror.LogMirror(gw, path)
    old = log_mirror.PULL_CHUNK
    log_mirror.PULL_CHUNK = 100
    calls = {"n": 0}

    def plan(kind, cat, payload):
        if kind == "read" and "rowid >" in str(payload):
            calls["n"] += 1
            if calls["n"] == 2:
                return "watchdog"
        return None
    gw.plan = plan
    try:
        mirror.refresh()
        first = "no error"
    except Exception as exc:
        first = type(exc).__name__ + ": " + str(exc)[:80]
    held_after_fail = mirror.count()
    gw.plan = None
    mirror.refresh()
    try:
        return {"first_pull": first, "rows_after_failed_pull": held_after_fail,
                "rows_after_resume": mirror.count(), "source_rows": 250,
                "state": mirror.state()}
    finally:
        log_mirror.PULL_CHUNK = old


if __name__ == "__main__":
    out = {}
    for name, fn in (("W1_snapshot", w1), ("W2_correction_lost_response", w2),
                     ("W3_api_status_cost", w3), ("W4_log_mirror_resume", w4)):
        try:
            out[name] = fn()
        except Exception as exc:
            import traceback
            out[name] = {"HARNESS_ERROR": traceback.format_exc()[-800:]}
        print(name, json.dumps(out[name], indent=1, default=str)[:1500])
    json.dump(out, open(os.path.join(os.path.dirname(__file__), "..", "web.json"), "w"),
              indent=1, default=str)
