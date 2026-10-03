"""Render a machine's activity panel with v3.9.0's OWN floor code.

    python v39_floor.py PLAN.json     (the v3.9.0 web server tree on sys.path)

A rollback (DG2, M5) leaves rows in LabCore that the rolled-back floor reads.
Counting them — once each, no bookkeeping kinds — says nothing about whether
that floor can READ them: critic, T-P5 round 3, found the copied-back
status_change rows carried a UTC offset, and v3.9's status gutter raised
"can't subtract offset-naive and offset-aware datetimes" (500) on every one.
Re-implementing the floor's arithmetic here would test the re-implementation,
so this script imports the TAGGED v3.9.0 `web_app`, puts exactly the rows
LabCore holds into its own FakeLabCoreGateway, lets the snapshot read them
once (as its poller would), and asks for
`/api/machines/<uid>/status-timeline` — the panel the floor opens.

PLAN.json: {"web_dir": ".../v3.9.0/LEM Web Server", "uid": "...",
            "rows": [{machine_uid, ts, kind, lab_id, test_name, value,
                      detail}, ...]}

Prints {"status": <HTTP status>, "events": n, "error": "..."} as JSON.
Talks to no network: the gateway is the fake, in this process.
"""
import json
import os
import sys

LOG_DDL = ("CREATE TABLE IF NOT EXISTS lem_machine_log (machine_uid TEXT, "
           "ts TEXT, kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, "
           "detail TEXT)")
COLS = ("machine_uid", "ts", "kind", "lab_id", "test_name", "value", "detail")


class _NoAuth:
    def login(self, u, p):
        return (None, "", "no")

    def logout(self, t):
        pass


def main(plan_path):
    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    web = plan["web_dir"]
    sys.path.insert(0, web)
    import web_app
    where = os.path.realpath(web_app.__file__)
    if not where.startswith(os.path.realpath(web)):
        raise SystemExit("v3.9 web_app loaded from %s, not the tagged tree"
                         % where)
    from labcore_gateway import FakeLabCoreGateway
    gw = FakeLabCoreGateway()
    gw.sql(LOG_DDL)
    for r in plan["rows"]:
        res = gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
                     [r.get(c) for c in COLS])
        if isinstance(res, dict) and res.get("error"):
            raise SystemExit("seeding the v3.9 floor failed: " + res["error"])
    app = web_app.create_app(gw, authenticator=_NoAuth(), secret="s")
    app.config["TESTING"] = True
    app.config["SNAPSHOTS"].refresh()
    try:
        # TESTING propagates the route's exception instead of a bare 500, so
        # the answer names the failure: an unhandled exception IS the 500
        # the floor would have shown.
        r = app.test_client().get("/api/machines/%s/status-timeline"
                                  % plan["uid"])
    except Exception as exc:                              # noqa: BLE001
        print(json.dumps({"status": 500, "events": 0,
                          "error": "%s: %s" % (type(exc).__name__, exc)}))
        return
    body = r.get_json(silent=True) or {}
    print(json.dumps({"status": r.status_code,
                      "events": len(body.get("events") or []),
                      "error": "" if r.status_code == 200
                      else r.get_data(as_text=True)[-400:]}))


if __name__ == "__main__":
    main(sys.argv[1])
