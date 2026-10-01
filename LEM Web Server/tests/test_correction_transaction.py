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


# ── a replay must still be true when it is given ────────────────────────────

class TestAReplayIsOnlyGivenWhileItIsStillTrue:
    """Round 3's critic, reproduced through the real `lem.js`: save 0.5 and
    lose the answer (the page keeps the id, as it must, because the save may
    have landed); save 0.6 (200); later save 0.5 again. The page keyed the
    kept id on method, URL and body, so the third press carried the FIRST
    press's id, and the ledger replayed its 200 "correction 0.5" with
    X-Request-Replayed — while `lem_correction_factors` held 0.6. The
    operator was told one factor is in force while the bench adds another
    to every reading: the recorded-versus-actual disagreement W2 exists to
    remove, produced by W2's own machinery.

    The ledger answers "what did this request do", but a replayed answer is
    read as "this is the state now". Those agree only until somebody changes
    the same correction again. So a stored answer is replayed only while
    the record still says what it says; otherwise the request is refused
    with 409, NOTHING is done (it may genuinely be a late retry, and doing it
    again would silently overwrite a colleague's later change), and the
    sentence says what is in force now and that pressing again makes the
    change. The page drops an id on any 4xx, so that press is a new change.

    The ledger is not consulted for this; the factor table is. A later change
    made WITHOUT an id (an older page, a script, another tab's fresh id)
    supersedes the answer just the same, and only the table sees all of
    them."""

    def _lost_then(self, store):
        def setup(app):
            app.config["PROPAGATE_EXCEPTIONS"] = False
            TestALostResponseAndARetry._lose_the_first_answer(None, app)
        app, c = _app(store, before_first_request=setup)
        first = c.post("/api/machines/m1/corrections", json=BODY,
                       headers={"X-Request-Id": "K1"})
        assert first.status_code == 500
        return c

    def test_the_critics_sequence_is_refused_not_replayed(self, store):
        c = self._lost_then(store)
        two = c.post("/api/machines/m1/corrections",
                     json=dict(BODY, correction=0.6),
                     headers={"X-Request-Id": "K2"})
        assert two.status_code == 200, two.get_json()
        three = c.post("/api/machines/m1/corrections", json=BODY,
                       headers={"X-Request-Id": "K1"})
        body = three.get_json()
        assert three.status_code == 409, body
        assert three.headers.get("X-Request-Replayed") is None
        assert body.get("ok") is not True
        assert body["superseded"] is True
        assert body["in_force"] == 0.6
        # The sentence names both numbers: what is in force, and what to
        # press again for. A bare "conflict" is a dead end.
        assert "0.6" in body["error"] and "0.5" in body["error"]
        assert "again" in body["error"]
        # Nothing was done: still 0.6, still exactly two changes.
        assert _factor(store) == 0.6
        assert counts(store) == (1, 2, 2)

    def test_pressing_again_with_a_new_id_makes_the_change(self, store):
        c = self._lost_then(store)
        c.post("/api/machines/m1/corrections", json=dict(BODY, correction=0.6),
               headers={"X-Request-Id": "K2"})
        c.post("/api/machines/m1/corrections", json=BODY,
               headers={"X-Request-Id": "K1"})
        r = c.post("/api/machines/m1/corrections", json=BODY,
                   headers={"X-Request-Id": "K3"})
        assert r.status_code == 200 and r.headers.get("X-Request-Replayed") is None
        assert _factor(store) == 0.5
        assert counts(store) == (1, 3, 3)

    def test_a_replay_that_is_still_true_is_still_a_replay(self, store):
        """0.5, then 0.6, then 0.5 again under a NEW id: the old 0.5 answer is
        true again, and replaying it does nothing a person would not want.
        The rule is "true now", not "nothing happened since"."""
        c = self._lost_then(store)
        c.post("/api/machines/m1/corrections", json=dict(BODY, correction=0.6),
               headers={"X-Request-Id": "K2"})
        c.post("/api/machines/m1/corrections", json=BODY,
               headers={"X-Request-Id": "K3"})
        r = c.post("/api/machines/m1/corrections", json=BODY,
                   headers={"X-Request-Id": "K1"})
        assert r.status_code == 200
        assert r.headers.get("X-Request-Replayed") == "true"
        assert counts(store) == (1, 3, 3)

    def test_a_change_made_without_an_id_supersedes_too(self, store):
        c = self._lost_then(store)
        c.post("/api/machines/m1/corrections", json=dict(BODY, correction=0.6))
        r = c.post("/api/machines/m1/corrections", json=BODY,
                   headers={"X-Request-Id": "K1"})
        assert r.status_code == 409, r.get_json()
        assert _factor(store) == 0.6

    def test_a_removal_since_supersedes_a_save(self, store):
        c = self._lost_then(store)
        assert c.delete("/api/machines/m1/corrections/Flash").status_code == 200
        r = c.post("/api/machines/m1/corrections", json=BODY,
                   headers={"X-Request-Id": "K1"})
        body = r.get_json()
        assert r.status_code == 409, body
        assert body["in_force"] is None
        assert "no correction" in body["error"].lower()
        assert _factor(store) is None

    def test_a_removal_is_not_replayed_over_a_later_save(self, store):
        """The DELETE twin: "removed" replayed while 0.6 is being added to
        every reading is the same lie with the sign flipped."""
        _app_, c = _app(store)
        c.post("/api/machines/m1/corrections", json=BODY)
        assert c.delete("/api/machines/m1/corrections/Flash",
                        headers={"X-Request-Id": "D1"}).status_code == 200
        c.post("/api/machines/m1/corrections", json=dict(BODY, correction=0.6))
        r = c.delete("/api/machines/m1/corrections/Flash",
                     headers={"X-Request-Id": "D1"})
        body = r.get_json()
        assert r.status_code == 409, body
        assert body["in_force"] == 0.6 and "0.6" in body["error"]
        assert _factor(store) == 0.6

    def test_the_check_is_made_under_the_writer_too(self, store):
        """The look-up before the work is cheap and unlocked; the one that
        decides is inside BEGIN IMMEDIATE. Simulated race: the unlocked
        look-up does not see K1 yet (the other copy has not committed), and
        by the time this copy holds the writer, K1 has committed AND a
        colleague has changed the factor to 0.9. The in-transaction look-up
        must apply the same "still true" rule, not replay blindly."""
        c = self._lost_then(store)
        real_read, real_tx = store.read_sql, store.transaction
        hidden = {"n": 0}

        def read_before_commit(sql, args=None, **kw):
            if "FROM request_ledger" in sql and hidden["n"] == 0:
                hidden["n"] += 1
                return {"rows": []}
            return real_read(sql, args, **kw)

        def tx_after_a_colleague_saves():
            store.sql("UPDATE lem_correction_factors SET correction = 0.9 "
                      "WHERE machine_uid = 'm1' AND test_name = 'Flash'")
            return real_tx()

        store.read_sql = read_before_commit
        store.transaction = tx_after_a_colleague_saves
        try:
            r = c.post("/api/machines/m1/corrections", json=BODY,
                       headers={"X-Request-Id": "K1"})
        finally:
            store.read_sql, store.transaction = real_read, real_tx
        assert hidden["n"] == 1
        assert r.status_code == 409, r.get_json()
        assert r.get_json()["in_force"] == 0.9
        assert _factor(store) == 0.9
        assert counts(store) == (1, 1, 1)


# ── a failure inside the transaction is a sentence about the right database ─

class TestAFailedTransactionSaysWhatHappened:
    """Two things round 3's critic found when it raised inside the
    transaction: an exception of a type the route did not name (a transport
    error out of the receipt's store) escaped as Flask's bare HTML 500,
    which the page can only render as "Internal Server Error"; and a store
    failure was worded "LabCore could not be written to … its state is
    unknown". Both are wrong about a rolled-back transaction on LEM's own
    disk: the state IS known (nothing changed, the previous factor is still
    in force), and LabCore was not involved. The rows were right (0/0/0);
    the words were not, and the words are what the supervisor acts on."""

    @pytest.mark.parametrize("boom", [
        ConnectionResetError("harness: the receipt's road reset"),
        OSError("harness: disk I/O"),
        KeyError("harness: a bug in the receipt"),
    ], ids=["transport", "os", "bug"])
    def test_any_exception_in_the_receipt_is_json_not_saved(self, store, boom,
                                                             monkeypatch):
        import equipment_history as ca

        def explode(self, **kw):
            raise boom
        monkeypatch.setattr(ca.CorrectionAuditStore, "record", explode)
        app, c = _app(store)
        app.config["PROPAGATE_EXCEPTIONS"] = False
        r = c.post("/api/machines/m1/corrections", json=BODY,
                   headers={"X-Request-Id": "boom"})
        assert r.is_json, r.get_data(as_text=True)[:200]
        body = r.get_json()
        assert r.status_code == 503, body
        assert body["saved"] is False
        assert "NOT saved" in body["error"]
        assert "LEM store" in body["error"]
        assert "LabCore" not in body["error"]
        assert "unknown" not in body["error"]       # rolled back: it IS known
        assert counts(store) == (0, 0, 0)
        monkeypatch.undo()
        again = c.post("/api/machines/m1/corrections", json=BODY,
                       headers={"X-Request-Id": "boom"})
        assert again.status_code == 200
        assert counts(store) == (1, 1, 1)

    def test_a_raised_store_write_is_not_called_labcore(self, store):
        real = store.sql

        def raise_on_log(sql, args=None, **kw):
            if "INSERT INTO lem_machine_log" in sql:
                raise OSError("harness: disk I/O error")
            return real(sql, args, **kw)

        store.sql = raise_on_log
        _app_, c = _app(store)
        r = c.post("/api/machines/m1/corrections", json=BODY)
        store.sql = real
        body = r.get_json()
        assert r.status_code == 503, body
        assert "LabCore" not in body["error"], body["error"]
        assert "LEM store" in body["error"]
        assert counts(store) == (0, 0, 0)

    def test_a_removal_too(self, store, monkeypatch):
        import equipment_history as ca
        _app_, c = _app(store)
        c.post("/api/machines/m1/corrections", json=BODY)

        def explode(self, **kw):
            raise ConnectionResetError("harness")
        monkeypatch.setattr(ca.CorrectionAuditStore, "record", explode)
        _app_.config["PROPAGATE_EXCEPTIONS"] = False
        r = c.delete("/api/machines/m1/corrections/Flash")
        assert r.is_json and r.status_code == 503, r.get_data(as_text=True)[:200]
        assert "NOT removed" in r.get_json()["error"]
        assert "LabCore" not in r.get_json()["error"]
        assert _factor(store) == 0.5
