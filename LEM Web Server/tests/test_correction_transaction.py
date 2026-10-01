"""W2: a correction factor, its audit receipt and its log line are ONE save.

Measured on v3.9.0 (`baseline/web.json`, W2): a correction-factor save whose
LabCore write landed but whose answer was lost told the supervisor
**503 "NOT saved … state is unknown"** — while the factor WAS in force and the
§7.8.2 receipt (`lem_correction_audit`) and the config log line were missing.
Pressing Save again then wrote the receipt, so the trail recorded one change
made by one person at the wrong time, and a third press would have recorded a
second change that never happened. The queue takes one statement at a time;
"factor, audit, log line" could not be one thing there.

On the LEM store it is one transaction: all three commit or none do. And a
retry of the SAME request — the browser re-sending after a lost response,
carrying the same `X-Request-Id` — is answered from `request_ledger`, written
inside that transaction, instead of being done twice. The bar is exactly
1 / 1 / 1 rows after a lost response and a retry.
"""

import json

import pytest
from flask import request

from labcore_counter import CountingLabCore
from labcore_gateway import FakeLabCoreGateway
from web_app import create_app


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


@pytest.fixture
def store():
    s = FakeLabCoreGateway()
    s.sql("CREATE TABLE IF NOT EXISTS lem_machine_status (machine_uid TEXT "
          "PRIMARY KEY, title TEXT, status TEXT, reason TEXT, updated_at TEXT)")
    s.sql("INSERT INTO lem_machine_status VALUES ('m1','PAC Flash 1','GREEN',"
          "'ok','2026-10-01T09:00:00')")
    return s


def _app(store, lab=None, before_first_request=None):
    app = create_app(store, labcore=lab or CountingLabCore(),
                     authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    if before_first_request is not None:
        before_first_request(app)
    c = app.test_client()
    c.post("/api/login", json={"username": "k", "password": "good"})
    return app, c


def counts(store):
    def n(sql):
        res = store.read_sql(sql)
        assert "error" not in res, res
        return res["rows"][0]["n"]
    return (n("SELECT COUNT(*) n FROM lem_correction_factors"),
            n("SELECT COUNT(*) n FROM lem_correction_audit"),
            n("SELECT COUNT(*) n FROM lem_machine_log WHERE kind = 'config' "
              "AND test_name = 'correction factor set'"))


BODY = {"test_name": "Flash", "correction": 0.5, "units": "C"}


class TestOneSaveIsOneTransaction:
    def test_a_clean_save_writes_all_three(self, store):
        _app_, c = _app(store)
        r = c.post("/api/machines/m1/corrections", json=BODY,
                   headers={"X-Request-Id": "req-1"})
        assert r.status_code == 200, r.get_json()
        assert counts(store) == (1, 1, 1)

    def test_a_refused_receipt_takes_the_factor_back_with_it(self, store):
        """The receipt cannot be refused after the factor landed any more:
        the receipt failing rolls the factor back, and the answer says NOT
        saved — which, for once, is exactly true."""
        real = store.sql

        def refuse_audit(sql, args=None, **kw):
            if "lem_correction_audit" in sql and sql.lstrip().upper().startswith("INSERT"):
                return {"error": "LabCore is busy — write queue is deep. "
                                 "Retry shortly.", "busy": True, "retry_after": 5}
            return real(sql, args, **kw)

        store.sql = refuse_audit
        _app_, c = _app(store)
        r = c.post("/api/machines/m1/corrections", json=BODY,
                   headers={"X-Request-Id": "req-2"})
        assert r.status_code == 503, r.get_json()
        assert "NOT saved" in r.get_json()["error"]
        store.sql = real
        assert counts(store) == (0, 0, 0)

    def test_a_refused_log_line_takes_both_back(self, store):
        real = store.sql

        def refuse_log(sql, args=None, **kw):
            if "INSERT INTO lem_machine_log" in sql:
                return {"error": "disk I/O error"}
            return real(sql, args, **kw)

        store.sql = refuse_log
        _app_, c = _app(store)
        r = c.post("/api/machines/m1/corrections", json=BODY)
        assert r.status_code >= 500, r.get_json()
        store.sql = real
        assert counts(store) == (0, 0, 0)


class TestALostResponseAndARetry:
    """W2 exactly: the transaction commits, the answer never reaches the
    browser, the browser sends the same request again."""

    def _lose_the_first_answer(self, app):
        lost = {"n": 0}

        @app.after_request
        def lose(response):
            if lost["n"] == 0 and response.status_code == 200 \
                    and "corrections" in request.path:
                lost["n"] += 1
                raise ConnectionResetError("harness: response lost after commit")
            return response
        return lost

    def test_the_retry_gives_one_one_one(self, store):
        def setup(app):
            app.config["PROPAGATE_EXCEPTIONS"] = False
            self._lose_the_first_answer(app)
        app, c = _app(store, before_first_request=setup)
        first = c.post("/api/machines/m1/corrections", json=BODY,
                       headers={"X-Request-Id": "req-lost"})
        assert first.status_code == 500          # the browser saw nothing useful
        assert counts(store) == (1, 1, 1)        # …but it all committed
        again = c.post("/api/machines/m1/corrections", json=BODY,
                       headers={"X-Request-Id": "req-lost"})
        assert again.status_code == 200, again.get_json()
        assert again.get_json()["ok"] is True
        assert again.headers.get("X-Request-Replayed") == "true"
        assert counts(store) == (1, 1, 1)

    def test_the_replay_is_the_original_answer(self, store):
        _app_, c = _app(store)
        one = c.post("/api/machines/m1/corrections", json=BODY,
                     headers={"X-Request-Id": "req-same"}).get_json()
        two = c.post("/api/machines/m1/corrections", json=BODY,
                     headers={"X-Request-Id": "req-same"}).get_json()
        assert one == two
        assert counts(store) == (1, 1, 1)

    def test_without_the_id_a_second_save_is_a_second_change(self, store):
        """Not a no-op by accident: two saves a person MEANT are two receipts.
        Only the same request id is the same request."""
        _app_, c = _app(store)
        c.post("/api/machines/m1/corrections", json=BODY)
        c.post("/api/machines/m1/corrections", json=BODY)
        assert counts(store) == (1, 2, 2)

    def test_a_different_id_is_a_different_request(self, store):
        _app_, c = _app(store)
        c.post("/api/machines/m1/corrections", json=BODY,
               headers={"X-Request-Id": "a"})
        c.post("/api/machines/m1/corrections",
               json=dict(BODY, correction=0.7), headers={"X-Request-Id": "b"})
        assert counts(store) == (1, 2, 2)

    def test_a_refused_save_leaves_no_ledger_entry_to_replay(self, store):
        """A failure is not remembered as a success: the retry after a
        refusal really does the work."""
        real = store.sql
        refused = {"n": 0}

        def refuse_once(sql, args=None, **kw):
            if "lem_correction_factors" in sql and refused["n"] == 0 and \
                    sql.lstrip().upper().startswith("INSERT"):
                refused["n"] += 1
                return {"error": "LabCore is busy", "busy": True, "retry_after": 1}
            return real(sql, args, **kw)

        store.sql = refuse_once
        _app_, c = _app(store)
        r1 = c.post("/api/machines/m1/corrections", json=BODY,
                    headers={"X-Request-Id": "req-r"})
        assert r1.status_code == 503
        r2 = c.post("/api/machines/m1/corrections", json=BODY,
                    headers={"X-Request-Id": "req-r"})
        assert r2.status_code == 200
        assert r2.headers.get("X-Request-Replayed") is None
        assert counts(store) == (1, 1, 1)

    def test_the_ledger_row_says_what_was_answered(self, store):
        _app_, c = _app(store)
        c.post("/api/machines/m1/corrections", json=BODY,
               headers={"X-Request-Id": "req-ledger"})
        row = store.read_sql("SELECT route, status, body FROM request_ledger "
                             "WHERE request_id = 'req-ledger'")["rows"][0]
        assert row["status"] == 200
        assert json.loads(row["body"])["test_name"] == "Flash"
        assert "corrections" in row["route"]


class TestRemovalIsOneTransactionToo:
    def test_remove_then_retry(self, store):
        _app_, c = _app(store)
        c.post("/api/machines/m1/corrections", json=BODY)
        r = c.delete("/api/machines/m1/corrections/Flash",
                     headers={"X-Request-Id": "del-1"})
        assert r.status_code == 200, r.get_json()
        again = c.delete("/api/machines/m1/corrections/Flash",
                         headers={"X-Request-Id": "del-1"})
        # Without the ledger this second press would be a 404 "No correction
        # for Flash" — a true sentence that tells the supervisor their first
        # press failed when it did not.
        assert again.status_code == 200, again.get_json()
        assert again.headers.get("X-Request-Replayed") == "true"
        n = store.read_sql("SELECT COUNT(*) n FROM lem_correction_audit"
                           )["rows"][0]["n"]
        assert n == 2                  # one set, one removal — not two removals


class TestItCostsLabCoreNothing:
    def test_a_save_is_zero_labcore_ops(self, store):
        lab = CountingLabCore()
        _app_, c = _app(store, lab)
        before = lab.ops                        # sign-in is the StubAuth's
        c.post("/api/machines/m1/corrections", json=BODY,
               headers={"X-Request-Id": "req-z"})
        assert lab.ops - before == 0, lab.calls


# ── what "the same request" means ───────────────────────────────────────────

def _factor(store):
    rows = store.read_sql("SELECT correction FROM lem_correction_factors "
                          "WHERE machine_uid = 'm1' AND test_name = 'Flash'"
                          )["rows"]
    return rows[0]["correction"] if rows else None


class TestTheIdIsScopedToOneRequest:
    """An X-Request-Id names ONE request: one method, one path, one body, one
    person. The first version looked the id up and nothing else, so the
    critic sent `DELETE /corrections/Flash` with an id a POST had used and got
    the POST's 200 back while the correction stayed in force: a supervisor
    told "removed" about an offset still being added to every reading.

    Reuse with anything different is refused (422, the answer the IETF
    Idempotency-Key draft gives for a key reused with a different request)
    and NOTHING is done: performing it would make the id mean two things,
    and replaying would answer a question nobody asked. The refusal names
    what the id was first used for, so a developer can find the bug."""

    def test_a_delete_with_a_posts_id_is_refused_and_removes_nothing(
            self, store):
        _app_, c = _app(store)
        assert c.post("/api/machines/m1/corrections", json=BODY,
                      headers={"X-Request-Id": "shared"}).status_code == 200
        r = c.delete("/api/machines/m1/corrections/Flash",
                     headers={"X-Request-Id": "shared"})
        assert r.status_code == 422, r.get_json()
        assert r.headers.get("X-Request-Replayed") is None
        assert "POST" in r.get_json()["error"]
        assert "nothing was done" in r.get_json()["error"].lower()
        assert _factor(store) == 0.5            # still in force, and says so
        # …and the removal the person meant works with its own id.
        assert c.delete("/api/machines/m1/corrections/Flash",
                        headers={"X-Request-Id": "own"}).status_code == 200
        assert _factor(store) is None

    def test_the_same_id_with_a_different_body_is_refused(self, store):
        _app_, c = _app(store)
        c.post("/api/machines/m1/corrections", json=BODY,
               headers={"X-Request-Id": "k"})
        r = c.post("/api/machines/m1/corrections",
                   json=dict(BODY, correction=0.9),
                   headers={"X-Request-Id": "k"})
        assert r.status_code == 422, r.get_json()
        assert _factor(store) == 0.5
        assert counts(store) == (1, 1, 1)

    def test_the_same_id_on_another_instrument_is_refused(self, store):
        store.sql("INSERT INTO lem_machine_status VALUES ('m2','PAC Flash 2',"
                  "'GREEN','ok','2026-10-01T09:00:00')")
        _app_, c = _app(store)
        c.post("/api/machines/m1/corrections", json=BODY,
               headers={"X-Request-Id": "k2"})
        r = c.post("/api/machines/m2/corrections", json=BODY,
                   headers={"X-Request-Id": "k2"})
        assert r.status_code == 422, r.get_json()

    def test_another_persons_id_is_not_their_answer(self, store):
        """The ledger body is the first person's answer. Somebody else
        presenting the same id gets a refusal, not a copy of it."""
        app, c = _app(store)
        c.post("/api/machines/m1/corrections", json=BODY,
               headers={"X-Request-Id": "mine"})
        other = app.test_client()
        other.post("/api/login", json={"username": "someone", "password":
                                       "good"})
        with other.session_transaction() as s:
            s["user"] = "someone-else"
        r = other.post("/api/machines/m1/corrections", json=BODY,
                       headers={"X-Request-Id": "mine"})
        assert r.status_code == 422, r.get_json()
        assert "test_name" not in (r.get_json() or {})

    def test_key_order_is_not_a_different_body(self, store):
        """The same JSON re-serialised by a retry is the same request."""
        _app_, c = _app(store)
        a = c.post("/api/machines/m1/corrections",
                   data='{"test_name":"Flash","correction":0.5,"units":"C"}',
                   content_type="application/json",
                   headers={"X-Request-Id": "ord"})
        b = c.post("/api/machines/m1/corrections",
                   data='{"units":"C","correction":0.5,"test_name":"Flash"}',
                   content_type="application/json",
                   headers={"X-Request-Id": "ord"})
        assert a.status_code == b.status_code == 200
        assert b.headers.get("X-Request-Replayed") == "true"
        assert counts(store) == (1, 1, 1)

    def test_the_ledger_row_records_the_scope(self, store):
        _app_, c = _app(store)
        c.post("/api/machines/m1/corrections", json=BODY,
               headers={"X-Request-Id": "scope"})
        row = store.read_sql("SELECT route, who, fingerprint FROM "
                             "request_ledger WHERE request_id = 'scope'"
                             )["rows"][0]
        assert row["route"] == "POST /api/machines/m1/corrections"
        assert row["who"] == "kaden"
        assert len(row["fingerprint"]) == 64


class TestConcurrentRetriesOfOneRequest:
    """The critic's 8 simultaneous POSTs with one id: 1 committed, 4
    replayed, and 3 were told 502 "was NOT saved and this instrument is still
    applying the previous one". It WAS saved; those three lost a race between
    the ledger look-up (outside the transaction) and the ledger INSERT
    (inside it), hit the primary key, and reported the loss as a refusal.
    "NOT saved" about a save that landed is the exact lie W2 exists to end.

    The look-up is now repeated INSIDE the transaction, which BEGIN IMMEDIATE
    serialises: whoever gets the writer second sees the first one's ledger
    row and answers with it."""

    def test_eight_at_once_are_one_change_and_eight_true_answers(self, store):
        import threading
        import time as _t
        real = store.sql

        def slow_factor(sql, args=None, **kw):
            # Hold the writer long enough that all eight pass the outer
            # look-up before the first one commits — the race, made certain.
            if "INSERT INTO lem_correction_factors" in sql:
                _t.sleep(0.3)
            return real(sql, args, **kw)

        store.sql = slow_factor
        app, _c = _app(store)
        clients = []
        for _ in range(8):
            cl = app.test_client()
            cl.post("/api/login", json={"username": "k", "password": "good"})
            clients.append(cl)
        gate = threading.Barrier(8)
        out = [None] * 8

        def go(i):
            gate.wait()
            r = clients[i].post("/api/machines/m1/corrections", json=BODY,
                                headers={"X-Request-Id": "burst"})
            out[i] = (r.status_code, r.headers.get("X-Request-Replayed"),
                      r.get_json())

        threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        store.sql = real
        statuses = sorted(o[0] for o in out)
        assert statuses == [200] * 8, out
        assert sum(1 for o in out if o[1] is None) == 1, out
        assert all(o[2] == out[0][2] for o in out), out
        assert counts(store) == (1, 1, 1)


class TestThePageSendsTheId:
    """The server half is worth nothing if no page sends the header: round
    one's critic found 0 senders. The correction editor's save and removal
    now go through `LEM.send`, which carries and reuses the id
    (tests/js/request_id.mjs pins its rules); this pins that the editor
    really calls it and no bare `fetch` write to the route is left."""

    def test_the_correction_editor_writes_through_lem_send(self):
        import os
        import re
        src = open(os.path.join(os.path.dirname(__file__), "..", "templates",
                                "floor.html"), encoding="utf-8").read()
        sends = re.findall(r"LEM\.send\(\s*`/api/machines/\$\{uid\}/"
                           r"corrections[^`]*`", src)
        assert len(sends) == 2, sends               # save and remove
        bare = re.findall(r"fetch\(\s*`/api/machines/\$\{uid\}/corrections"
                          r"[^`]*`\s*,\s*\{\s*method", src)
        assert bare == [], bare

    def test_lem_send_sets_the_header(self):
        import os
        src = open(os.path.join(os.path.dirname(__file__), "..", "static",
                                "lem.js"), encoding="utf-8").read()
        assert "'X-Request-Id': id" in src
