"""No row leaves the effective record without an approval, and reinstating
brings it back.

Transfer spec §10.5 and decision D7: hiding a replayed row is Ryan's call, per
bench, recorded as an audited `annotation_approval` row (who, when, what was
shown). "There is no automatic hiding anywhere."

That sentence used to be a promise kept by the CALLERS — `log_annotation`
took any `replay_duplicate` row from anyone, and the effective view hid the
reading. Every statement that reached the store could take a reading out of
every QC chart without leaving an approval behind. So the rule now lives in
the FILE, where the sqlite3 shell meets it too:

* a hiding annotation is refused unless it names an `approved` approval for
  the SAME bench and the same rule, signed by a person;
* approvals are append-only — the evidence for a hide cannot be edited or
  deleted afterwards;
* and the view itself hides a row only through such an approval, so even an
  annotation that got past a dropped trigger hides nothing.

The property test at the bottom walks random sequences of approvals, applies,
reinstatements and forged writes, and checks the invariant after every step.
"""

import json
import random
import sqlite3

import pytest

import dedupe
import dedupe_sim
from dedupe_sim import DUP, GENUINE
from lem_store import LocalStoreGateway


@pytest.fixture
def store(tmp_path):
    s = LocalStoreGateway(str(tmp_path / "lem.db"))
    yield s
    s.close()


def _load(store, rows):
    for r in rows:
        res = store.sql(
            "INSERT INTO lem_machine_log (id, machine_uid, ts, kind, lab_id, "
            "test_name, value, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [r["id"], r["machine_uid"], r["ts"], r["kind"], r["lab_id"],
             r["test_name"], r["value"], r["detail"]])
        assert "error" not in res, res


def _effective(store):
    res = store.read_sql("SELECT id FROM lem_machine_log_effective")
    assert "error" not in res, res
    return {r["id"] for r in res["rows"]}


def _approval(store, uid="m1", rule="replay_duplicate", decision="approved",
              by="ryan", at="2026-10-01T09:00:00"):
    res = store.sql(
        "INSERT INTO annotation_approval (machine_uid, rule, run_id, "
        "candidates, approved_by, approved_at, decision) "
        "VALUES (?, ?, 'upto=1;sha=x', 1, ?, ?, ?)",
        [uid, rule, by, at, decision])
    assert "error" not in res, res
    return store.read_sql("SELECT MAX(id) AS i FROM annotation_approval"
                          )["rows"][0]["i"]


def _log(store, uid="m1", lab="L1"):
    res = store.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                    "lab_id, value, detail) VALUES (?, '2026-09-01T00:00:00', "
                    "'run', ?, '1', '{}')", [uid, lab])
    assert "error" not in res
    return store.read_sql("SELECT MAX(id) AS i FROM lem_machine_log_effective"
                          )["rows"][0]["i"]


def _hide(store, rid, label="replay_duplicate", approval_id=None):
    return store.sql(
        "INSERT INTO log_annotation (log_id, label, by, at, approval_id) "
        "VALUES (?, ?, 'someone', 't', ?)", [rid, label, approval_id])


class TestNoHideWithoutAnApproval:
    """Each way a hiding annotation could be written without Ryan's say-so,
    refused by the file itself — and the reading still counts afterwards."""

    @pytest.mark.parametrize("label", ["replay_duplicate", "import_leftover"])
    def test_no_approval_named(self, store, label):
        rid = _log(store)
        res = _hide(store, rid, label)
        assert "approval" in res.get("error", ""), res
        assert rid in _effective(store)

    def test_an_approval_that_does_not_exist(self, store):
        rid = _log(store)
        assert "approval" in _hide(store, rid, approval_id=999)["error"]
        assert rid in _effective(store)

    def test_a_rejected_approval(self, store):
        rid = _log(store)
        no = _approval(store, decision="rejected")
        assert "approval" in _hide(store, rid, approval_id=no)["error"]
        assert rid in _effective(store)

    def test_another_benchs_approval(self, store):
        """Approval is per bench: GC 1's approval cannot hide an NIR row."""
        rid = _log(store, uid="m2")
        other = _approval(store, uid="m1")
        assert "approval" in _hide(store, rid, approval_id=other)["error"]
        assert rid in _effective(store)

    def test_an_approval_for_a_different_rule(self, store):
        """Approving the import leftovers is not approving the replays."""
        rid = _log(store)
        imp = _approval(store, rule="import_leftover")
        assert "approval" in _hide(store, rid, approval_id=imp)["error"]
        assert rid in _effective(store)

    def test_an_approval_nobody_signed_is_refused_at_the_door(self, store):
        for by, at in ((None, "t"), ("", "t"), ("  ", "t"), ("ryan", None)):
            res = store.sql(
                "INSERT INTO annotation_approval (machine_uid, rule, run_id, "
                "approved_by, approved_at, decision) VALUES "
                "('m1', 'replay_duplicate', 'r', ?, ?, 'approved')", [by, at])
            assert "error" in res, (by, at)

    def test_a_decision_must_be_one_of_the_two(self, store):
        res = store.sql(
            "INSERT INTO annotation_approval (machine_uid, rule, run_id, "
            "approved_by, approved_at, decision) VALUES "
            "('m1', 'replay_duplicate', 'r', 'ryan', 't', 'maybe')")
        assert "error" in res

    def test_the_bare_file_meets_the_same_refusal(self, store):
        rid = _log(store)
        con = sqlite3.connect(store.path)
        try:
            with pytest.raises(sqlite3.DatabaseError, match="approval"):
                con.execute("INSERT INTO log_annotation (log_id, label, by, "
                            "at) VALUES (?, 'replay_duplicate', 'x', 't')",
                            [rid])
        finally:
            con.close()
        assert rid in _effective(store)

    def test_visible_labels_need_no_approval(self, store):
        """Listing a row for review hides nothing, so it asks nobody."""
        rid = _log(store)
        for label in ("probable_duplicate", "replay_candidate",
                      "ambiguous_repeat"):
            assert "error" not in _hide(store, rid, label)
        assert rid in _effective(store)

    def test_a_review_label_after_a_hide_does_not_unhide(self, store):
        """Only a hide or a `reinstated` decides. A later review note
        (`probable_duplicate`, `replay_candidate`) is not a reinstatement,
        and must not act as one by being the newest row."""
        rid = _log(store)
        assert "error" not in _hide(store, rid, approval_id=_approval(store))
        assert "error" not in _hide(store, rid, "replay_candidate")
        assert rid not in _effective(store)

    def test_with_an_approval_the_row_leaves_and_reinstated_brings_it_back(
            self, store):
        rid = _log(store)
        ok = _approval(store)
        assert "error" not in _hide(store, rid, approval_id=ok)
        assert rid not in _effective(store)
        assert "error" not in store.sql(
            "INSERT INTO log_annotation (log_id, label, by, at) "
            "VALUES (?, 'reinstated', 'ryan', 't')", [rid])
        assert rid in _effective(store)


class TestApprovalsAreTheRecordOfTheDecision:
    def test_an_approval_cannot_be_edited_or_deleted(self, store):
        aid = _approval(store)
        assert "append-only" in store.sql(
            "UPDATE annotation_approval SET decision = 'rejected'")["error"]
        assert "append-only" in store.sql(
            "UPDATE annotation_approval SET approved_by = 'someone else'"
        )["error"]
        assert "append-only" in store.sql(
            "DELETE FROM annotation_approval")["error"]
        res = store.sql("INSERT OR REPLACE INTO annotation_approval (id, "
                        "machine_uid, rule, run_id, approved_by, approved_at, "
                        "decision) VALUES (?, 'm1', 'replay_duplicate', 'r', "
                        "'x', 't', 'approved')", [aid])
        assert "already in the record" in res["error"]

    def test_the_view_needs_the_approval_even_if_the_trigger_was_bypassed(
            self, store, tmp_path):
        """Defence in depth. The bare file can drop a trigger (SQLite has no
        rule against its owner), write an unapproved hide, and the store's
        next open re-declares the trigger — but cannot know the annotation
        was forged. The VIEW therefore checks the approval too, so that
        annotation hides nothing."""
        rid = _log(store)
        path = store.path
        store.close()
        con = sqlite3.connect(path)
        con.execute("DROP TRIGGER ann_hide_needs_approval")
        con.execute("INSERT INTO log_annotation (log_id, label, by, at) "
                    "VALUES (?, 'replay_duplicate', 'forger', 't')", [rid])
        con.commit()
        con.close()
        again = LocalStoreGateway(path)
        try:
            assert "ann_hide_needs_approval" in again.health()[
                "guards_repaired"] or again.health()["guards_missing"] == []
            assert rid in _effective(again)
        finally:
            again.close()


# ── the flow end to end, on the synthetic lab ───────────────────────────────

@pytest.fixture
def sim_store(store):
    lab = dedupe_sim.build()
    _load(store, lab.rows)
    return store, lab


def _approve_all(store, report, by="ryan"):
    ids = []
    for bench in report["benches"]:
        for label, n in bench["candidates"].items():
            if label in dedupe.HIDE_CANDIDATE_LABELS and n:
                ids.append(dedupe.approve(
                    store, bench["machine_uid"], label,
                    bench["run_ids"][label], approved_by=by)["approval_id"])
    return ids


class TestTheFlowOnTheSyntheticLab:
    def test_a_dry_run_writes_nothing(self, sim_store):
        store, lab = sim_store
        before = store.read_sql("SELECT COUNT(*) n FROM log_annotation"
                                )["rows"][0]["n"]
        report = dedupe.dry_run(store)
        assert report["hide_candidates"] > 0
        assert store.read_sql("SELECT COUNT(*) n FROM log_annotation"
                              )["rows"][0]["n"] == before == 0
        assert store.read_sql("SELECT COUNT(*) n FROM annotation_approval"
                              )["rows"][0]["n"] == 0

    def test_approve_and_apply_everything_hides_exactly_the_duplicates(
            self, sim_store):
        """The bar: 100 % of the replays hidden, 0 genuine rows hidden — the
        identical re-tests, the QC repeat and the archive first-ingest
        included — measured on the effective VIEW every reader uses."""
        store, lab = sim_store
        report = dedupe.dry_run(store)
        for aid in _approve_all(store, report):
            dedupe.apply(store, aid, by="ryan")
        effective = _effective(store)
        run_qc = {r["id"] for r in lab.rows if r["kind"] in ("run", "qc")}
        assert lab.ids(DUP) & effective == set()
        assert (lab.ids(GENUINE) & run_qc) - effective == set()
        # and every hidden row carries the approval that hid it
        hidden = store.read_sql(
            "SELECT a.log_id, a.approval_id, p.approved_by FROM log_annotation"
            " a JOIN annotation_approval p ON p.id = a.approval_id"
            " WHERE a.label IN ('replay_duplicate', 'import_leftover')")
        assert {r["log_id"] for r in hidden["rows"]} == lab.ids(DUP)
        assert {r["approved_by"] for r in hidden["rows"]} == {"ryan"}

    def test_nothing_is_hidden_until_each_bench_is_approved(self, sim_store):
        store, lab = sim_store
        report = dedupe.dry_run(store)
        era = next(b for b in report["benches"] if b["machine_uid"] == "era")
        aid = dedupe.approve(store, "era", "replay_duplicate",
                             era["run_ids"]["replay_duplicate"],
                             approved_by="ryan")["approval_id"]
        dedupe.apply(store, aid, by="ryan")
        gone = {r["id"] for r in lab.rows} - _effective(store)
        era_ids = {r["id"] for r in lab.bench_rows("era")}
        assert gone and gone <= era_ids

    def test_a_rejection_is_recorded_and_hides_nothing(self, sim_store):
        store, lab = sim_store
        report = dedupe.dry_run(store)
        era = next(b for b in report["benches"] if b["machine_uid"] == "era")
        res = dedupe.approve(store, "era", "replay_duplicate",
                             era["run_ids"]["replay_duplicate"],
                             approved_by="ryan", decision="rejected")
        with pytest.raises(dedupe.DedupeRefused, match="rejected"):
            dedupe.apply(store, res["approval_id"], by="ryan")
        assert _effective(store) == {r["id"] for r in lab.rows}

    def test_the_approval_carries_what_was_shown(self, sim_store):
        store, _lab = sim_store
        report = dedupe.dry_run(store)
        gc = next(b for b in report["benches"] if b["machine_uid"] == "gc")
        aid = dedupe.approve(store, "gc", "replay_duplicate",
                             gc["run_ids"]["replay_duplicate"],
                             approved_by="ryan")["approval_id"]
        row = store.read_sql("SELECT * FROM annotation_approval WHERE id = ?",
                             [aid])["rows"][0]
        assert row["candidates"] == gc["candidates"]["replay_duplicate"]
        assert row["approved_by"] == "ryan" and row["approved_at"]
        shown = json.loads(row["examples"])          # the pairs Ryan saw
        assert len(shown) == min(20, gc["candidates"]["replay_duplicate"])
        assert {e["label"] for e in shown} == {"replay_duplicate"}
        impact = json.loads(row["qc_impact"])
        assert any(s["test_name"] == "Sulfur" for s in impact)

    def test_approving_a_report_the_record_has_moved_past_is_refused(
            self, sim_store):
        """The approval names the candidate set by digest. If the record
        changed underneath the report (a new burst landed), the approver
        approved something that no longer exists, and is told so."""
        store, lab = sim_store
        report = dedupe.dry_run(store)
        era = next(b for b in report["benches"] if b["machine_uid"] == "era")
        stale = era["run_ids"]["replay_duplicate"]
        upto, _sha = dedupe.parse_run_id(stale)
        forged = "upto=%d;sha=%s" % (upto, "0" * 64)
        with pytest.raises(dedupe.DedupeRefused, match="changed"):
            dedupe.approve(store, "era", "replay_duplicate", forged,
                           approved_by="ryan")

    def test_rows_that_arrive_after_the_report_are_not_touched(
            self, sim_store):
        store, lab = sim_store
        report = dedupe.dry_run(store)
        era = next(b for b in report["benches"] if b["machine_uid"] == "era")
        aid = dedupe.approve(store, "era", "replay_duplicate",
                             era["run_ids"]["replay_duplicate"],
                             approved_by="ryan")["approval_id"]
        # Another restart replay lands between approval and apply.
        era_rows = [r for r in lab.bench_rows("era") if r["kind"] == "run"]
        late_ids = []
        for r in era_rows[:25]:
            store.sql("INSERT INTO lem_machine_log (machine_uid, ts, kind, "
                      "lab_id, test_name, value, detail) VALUES (?, "
                      "'2026-09-30T00:00:00', ?, ?, ?, ?, ?)",
                      [r["machine_uid"], r["kind"], r["lab_id"],
                       r["test_name"], r["value"], r["detail"]])
            late_ids.append(store.read_sql(
                "SELECT MAX(id) i FROM lem_machine_log_effective"
            )["rows"][0]["i"])
        out = dedupe.apply(store, aid, by="ryan")
        assert out["annotated"] == era["candidates"]["replay_duplicate"]
        assert set(late_ids) <= _effective(store)

    def test_apply_twice_annotates_once(self, sim_store):
        store, _lab = sim_store
        report = dedupe.dry_run(store)
        aid = _approve_all(store, report)[0]
        first = dedupe.apply(store, aid, by="ryan")
        n = store.read_sql("SELECT COUNT(*) n FROM log_annotation"
                           )["rows"][0]["n"]
        second = dedupe.apply(store, aid, by="ryan")
        assert first["annotated"] > 0 and second["annotated"] == 0
        assert store.read_sql("SELECT COUNT(*) n FROM log_annotation"
                              )["rows"][0]["n"] == n

    def test_an_apply_cut_short_is_finished_by_running_it_again(
            self, sim_store, monkeypatch):
        """Apply commits in chunks so it never holds the store's one writer
        for long. A failure between chunks leaves whole chunks applied and
        nothing half-written; the same apply again completes the set."""
        store, lab = sim_store
        monkeypatch.setattr(dedupe, "APPLY_CHUNK", 10)
        report = dedupe.dry_run(store)
        era = next(b for b in report["benches"] if b["machine_uid"] == "era")
        want = era["candidates"]["replay_duplicate"]
        aid = dedupe.approve(store, "era", "replay_duplicate",
                             era["run_ids"]["replay_duplicate"],
                             approved_by="ryan")["approval_id"]
        real, calls = store.sql, []

        def flaky(sql, args=None, **kw):
            if sql.startswith("INSERT INTO log_annotation"):
                calls.append(1)
                if len(calls) == 25:
                    return {"error": "disk I/O error"}
            return real(sql, args, **kw)
        store.sql = flaky
        with pytest.raises(dedupe.DedupeRefused, match="disk I/O"):
            dedupe.apply(store, aid, by="ryan")
        store.sql = real
        n = store.read_sql("SELECT COUNT(*) n FROM log_annotation"
                           )["rows"][0]["n"]
        assert n == 20                       # two whole chunks, no part
        out = dedupe.apply(store, aid, by="ryan")
        assert out["annotated"] == want - 20 and out["already_hidden"] == 20
        assert lab.ids(DUP) & {r["id"] for r in lab.bench_rows("era")} \
            & _effective(store) == set()

    def test_reinstated_restores_and_a_later_apply_does_not_rehide(
            self, sim_store):
        """A reinstatement is a person's decision about ONE reading. A later
        approval of the same bench must not quietly undo it."""
        store, lab = sim_store
        report = dedupe.dry_run(store)
        for aid in _approve_all(store, report):
            dedupe.apply(store, aid, by="ryan")
        victim = sorted(lab.ids(DUP))[0]
        assert victim not in _effective(store)
        out = dedupe.reinstate(store, [victim], by="ryan",
                               reason="checked the printout: a real re-run")
        assert out["reinstated"] == [victim]
        assert victim in _effective(store)
        # history is kept: the hide and the reinstatement are both there
        labels = [r["label"] for r in store.read_sql(
            "SELECT label FROM log_annotation WHERE log_id = ? ORDER BY id",
            [victim])["rows"]]
        assert labels[-1] == "reinstated" and len(labels) == 2
        # a fresh report and approval for that bench leaves it visible
        uid = next(r["machine_uid"] for r in lab.rows if r["id"] == victim)
        report = dedupe.dry_run(store, machine_uid=uid)
        bench = report["benches"][0]
        for label, rid in bench["run_ids"].items():
            if bench["candidates"].get(label):
                aid = dedupe.approve(store, uid, label, rid,
                                     approved_by="ryan")["approval_id"]
                dedupe.apply(store, aid, by="ryan")
        assert victim in _effective(store)

    def test_reinstating_a_row_that_is_not_hidden_says_so(self, sim_store):
        store, lab = sim_store
        some = sorted(lab.ids(GENUINE))[0]
        out = dedupe.reinstate(store, [some], by="ryan", reason="x")
        assert out["reinstated"] == [] and out["not_hidden"] == [some]

    def test_reinstating_needs_a_reason_and_a_person(self, sim_store):
        store, _lab = sim_store
        for by, reason in (("", "r"), ("ryan", ""), ("ryan", "   ")):
            with pytest.raises(dedupe.DedupeRefused):
                dedupe.reinstate(store, [1], by=by, reason=reason)

    def test_dedupe_never_runs_on_labcore(self):
        """§10.5: 'It never runs on LabCore.' A gateway that is not LEM's
        store is refused before a single statement is sent."""
        # LabCore's own fake: in this suite `FakeLabCoreGateway` IS the store.
        from labcore_gateway import InMemoryLabCore
        gw = InMemoryLabCore()
        sent = []
        gw.read_sql = lambda *a, **k: sent.append(a) or {"rows": []}
        gw.sql = lambda *a, **k: sent.append(a) or {"ok": True}
        for call in (lambda: dedupe.dry_run(gw),
                     lambda: dedupe.approve(gw, "m", "replay_duplicate", "r",
                                            approved_by="ryan"),
                     lambda: dedupe.apply(gw, 1, by="ryan"),
                     lambda: dedupe.reinstate(gw, [1], by="ryan", reason="x")):
            with pytest.raises(dedupe.DedupeRefused, match="LabCore"):
                call()
        assert sent == []


class TestAFailedReadIsNeverAnEmptyResult:
    def test_a_failed_read_raises_rather_than_reporting_no_duplicates(
            self, store):
        def broken(*_a, **_k):
            return {"error": "database is locked"}
        store.read_sql = broken
        with pytest.raises(dedupe.DedupeReadError, match="locked"):
            dedupe.dry_run(store)

    def test_a_part_filled_mirror_copy_is_refused(self, tmp_path):
        path = tmp_path / "mirror.sqlite3"
        con = sqlite3.connect(str(path))
        con.execute("CREATE TABLE log (rowid_src INTEGER PRIMARY KEY, "
                    "machine_uid, ts, kind, lab_id, test_name, value, detail)")
        con.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
        con.execute("INSERT INTO meta VALUES ('stale_reason', 'timed out')")
        con.commit()
        con.close()
        with pytest.raises(dedupe.DedupeReadError, match="timed out"):
            dedupe.read_mirror(str(path))

    def test_a_missing_mirror_copy_is_refused(self, tmp_path):
        with pytest.raises(dedupe.DedupeReadError):
            dedupe.read_mirror(str(tmp_path / "nope.sqlite3"))


# ── the invariant, under random operations ──────────────────────────────────

def _invariant(store):
    """Every row missing from the effective view is missing because its
    newest DECIDING annotation (a hide or a `reinstated`; review notes decide
    nothing) hides it under an approved approval for its own bench — or
    because its machine was retired (not exercised here)."""
    res = store.read_sql(
        # raw-log: the invariant is about the rows the view leaves out.
        "SELECT l.id, l.machine_uid FROM lem_machine_log l "
        "WHERE l.id NOT IN (SELECT id FROM lem_machine_log_effective)")
    assert "error" not in res, res
    for row in res["rows"]:
        newest = store.read_sql(
            "SELECT a.label, p.decision, p.machine_uid, p.approved_by "
            "FROM log_annotation a LEFT JOIN annotation_approval p "
            "ON p.id = a.approval_id WHERE a.log_id = ? AND a.label IN "
            "('replay_duplicate', 'import_leftover', 'reinstated') "
            "ORDER BY a.id DESC LIMIT 1", [row["id"]])["rows"]
        assert newest, row
        n = newest[0]
        assert n["label"] in ("replay_duplicate", "import_leftover"), row
        assert n["decision"] == "approved", row
        assert n["machine_uid"] == row["machine_uid"], row
        assert (n["approved_by"] or "").strip(), row


@pytest.mark.parametrize("seed", range(6))
def test_no_row_leaves_the_effective_view_without_an_approval(store, seed):
    rnd = random.Random(seed)
    lab = dedupe_sim.build()
    _load(store, lab.rows)
    ids = [r["id"] for r in lab.rows]
    uids = sorted({r["machine_uid"] for r in lab.rows})
    for _step in range(40):
        op = rnd.choice(["forge", "forge_other", "approve_apply", "reinstate",
                         "reject", "visible"])
        rid = rnd.choice(ids)
        if op == "forge":
            _hide(store, rid, rnd.choice(["replay_duplicate",
                                          "import_leftover"]),
                  approval_id=rnd.choice([None, 0, 10 ** 6]))
        elif op == "forge_other":
            aid = _approval(store, uid=rnd.choice(uids))
            _hide(store, rid, "replay_duplicate", approval_id=aid)
        elif op == "reject":
            aid = _approval(store, uid=rnd.choice(uids), decision="rejected")
            _hide(store, rid, approval_id=aid)
        elif op == "visible":
            _hide(store, rid, "probable_duplicate")
        elif op == "approve_apply":
            uid = rnd.choice(uids)
            report = dedupe.dry_run(store, machine_uid=uid)
            for bench in report["benches"]:
                for label, run_id in bench["run_ids"].items():
                    if bench["candidates"].get(label):
                        aid = dedupe.approve(store, uid, label, run_id,
                                             approved_by="ryan")["approval_id"]
                        dedupe.apply(store, aid, by="ryan")
        elif op == "reinstate":
            hidden = sorted(set(ids) - _effective(store))
            if hidden:
                dedupe.reinstate(store, [rnd.choice(hidden)], by="ryan",
                                 reason="random walk")
        _invariant(store)
