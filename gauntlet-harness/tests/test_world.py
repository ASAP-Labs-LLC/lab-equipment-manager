"""The World: truth registered from every print, kills that must land.

Two ways a fault harness lies without anyone noticing, both pinned here:

* A genuine print that the harness did not REGISTER is invisible to the tally
  — it can be lost and the scenario still reads 0/0. Phase 1's scenarios
  record prints both through `emit()` and by assigning `c.printed[...]`
  directly, so World hooks the dict, and `emit_line` covers repeats.

* A kill scenario whose kill never fired measured an uneventful run and
  reports it as crash-safety. World refuses: an armed kill point that was not
  reached is an error, never a pass.
"""
import pytest


@pytest.fixture
def W(loaded):
    lh, rf, rw, mod, GG = loaded
    from gharness.world import make_world
    from gharness.servers import server_factory
    World = make_world(lh, rf, mod, GG, server_factory)
    rf.Ctx = World
    return World


def test_every_print_path_registers_truth(W):
    c = W()
    c.emit(2)                                   # baseline emit -> printed[...] =
    c.printed["100126-19999"] = "0.1234"        # direct assignment (R1's way)
    c.emit_line("QC-1", "0.5"); c.emit_line("QC-1", "0.5")   # a genuine repeat
    assert c.ledger.truth() == {("100126-10000", "0.8000"): 1,
                                ("100126-10001", "0.8001"): 1,
                                ("100126-19999", "0.1234"): 1,
                                ("QC-1", "0.5"): 2}


def test_rewrite_and_rotate_register_nothing(W):
    c = W()
    c.emit(3)
    n = len(c.ledger)
    c.rewrite(c.lines()[1:])
    c.rotate(["x,1\n"])
    assert len(c.ledger) == n


def test_a_clean_world_tallies_zero_and_matches_phase_one(W):
    c = W()
    for _ in range(3):
        c.emit(3); c.poll()
    c.settle()
    t = c.tally("t", "t")
    assert (t["lost"], t["dup"]) == (0, 0)
    assert t["truth_prints"] == t["stored_effective"] == 9
    if c.store_kind() == "lem":
        # A v2 bench (P8) keeps its record in LEM's store, and the phase-1
        # columns count LabCore's lem_machine_log — which v2 leaves alone.
        # That they read "all 9 lost" is the point, not a fault: LabCore was
        # spared every one of these rows.
        assert (t["log_lost"], t["log_rows"]) == (9, 0)
        assert t["v2_syncs"] >= 1
    else:
        assert (t["log_lost"], t["log_dup"]) == (0, 0)


def test_an_armed_kill_that_is_never_reached_is_an_error(W, loaded):
    from gharness.world import KillNeverReached
    c = W()
    c.emit(3)
    # A point this worktree has plumbing for but no call site yet: the
    # combined guard read comes with P3. (P1's journal points are reached now —
    # K1b, K8r and T5 kill through them.)
    c.kill_at("after_combined_read")
    c.poll()
    with pytest.raises(KillNeverReached) as e:
        c.assert_killed("after_combined_read")
    assert "never called" in str(e.value)


def test_a_reached_kill_point_raises_the_baseline_kill_and_restarts(W, loaded):
    lh, rf, rw, mod, GG = loaded
    c = W()
    c.emit(3); c.poll()
    c.kill_at("after_cursor")
    # Stand in for P1's call site: the poll calls the point once.
    real = c.m.process_now

    def process_now(now):
        c.m._fault_point("after_cursor")
        return real(now)
    c.m.process_now = process_now
    c.emit(3)
    c.poll()
    c.assert_killed("after_cursor")
    assert (c.kills, c.restarts) == (1, 1)


def test_the_t0_mutation_deletes_one_stored_row(W):
    c = W()
    c.emit(3); c.poll(); c.settle()
    W.mutation = "T0"
    try:
        t = c.tally("t", "t")
    finally:
        W.mutation = None
    assert (t["lost"], t["stored_effective"]) == (1, 2)
    # On LabCore T0 is a real DELETE (phase-1 columns see it too); on a v2
    # bench's LEM store it drops at read, and LabCore holds none of the rows.
    assert t["log_lost"] == (1 if c.store_kind() == "labcore" else 3)


def test_unmeasurable_counters_are_none_not_zero(W):
    # A bench that never spoke v2 (an old server answers 404 from the start):
    # nothing reached a LEM store, there is no expect and no v2 sync.
    c = W(road_modes={"A": "404", "B": "404"})
    c.emit(1); c.poll()
    t = c.tally("t", "t")
    assert t["conflicts"] is None
    assert t["expect_audit_hits"] is None
    assert t["records_resent"] is None


# ── the LEM store (v4) side of the tally, and T0 on it ─────────────────────
#
# On v3.9 every stored row is in the fake LabCore, so the LEM-store reader and
# T0's drop-at-read branch never ran in a gate run. They are what the v4 gate
# will lean on the day P6 lands a store, so they are run here against a store
# built to the spec §6 shape: lem_machine_log (append-only; T0 cannot DELETE,
# the triggers forbid it, so it drops the row at read) and log_annotation,
# whose LATEST label for a row decides whether it is hidden.

SPEC_STORE = """
CREATE TABLE lem_machine_log (id INTEGER PRIMARY KEY, machine_uid TEXT, ts TEXT,
  kind TEXT, lab_id TEXT, test_name TEXT, value TEXT, detail TEXT);
CREATE TRIGGER lem_log_no_delete BEFORE DELETE ON lem_machine_log
  BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TABLE log_annotation (id INTEGER PRIMARY KEY, log_id INTEGER NOT NULL,
  label TEXT NOT NULL);
CREATE TABLE result_conflict (id INTEGER PRIMARY KEY);
"""


def _lem_store_world(W, rows, annotations=()):
    """A World whose server says it has a LEM store, holding `rows`
    [(lab_id, value)] for its bench, in order."""
    import json
    import os
    import sqlite3
    import types
    # Roads answer 404, so the bench never builds the real server's store:
    # this store is the hand-made one below, and nothing else writes it.
    c = W(road_modes={"A": "404", "B": "404"})
    c.emit(3)
    con = sqlite3.connect(os.environ["LEM_STORE_PATH"])
    con.executescript(SPEC_STORE)
    for lab, val in rows:
        con.execute("INSERT INTO lem_machine_log (machine_uid, kind, lab_id, detail) "
                    "VALUES (?, 'run', ?, ?)",
                    [c.uid, lab, json.dumps({"raw": {"Density": val}})])
    for log_id, label in annotations:
        con.execute("INSERT INTO log_annotation (log_id, label) VALUES (?, ?)",
                    [log_id, label])
    con.commit()
    con.close()
    c.server._app = types.SimpleNamespace(config={"LEM_STORE": True})
    c.record_in = "lem"
    assert c.store_kind() == "lem"
    return c


TRUTH3 = [("100126-10000", "0.8000"), ("100126-10001", "0.8001"),
          ("100126-10002", "0.8002")]


def test_the_lem_store_tally_counts_hidden_replays_as_not_stored(W):
    c = _lem_store_world(W, TRUTH3 + [TRUTH3[0]], annotations=[(4, "replay_duplicate")])
    t = c.tally("t", "t")
    assert (t["lost"], t["dup"], t["stored_effective"], t["truth_prints"]) == (0, 0, 3, 3)
    assert t["conflicts"] == 0          # read from the store, not None


def test_a_reinstated_row_counts_again(W):
    # Hidden-ness is the LATEST annotation, not any annotation.
    c = _lem_store_world(W, TRUTH3 + [TRUTH3[0]],
                         annotations=[(4, "replay_duplicate"), (4, "reinstated")])
    t = c.tally("t", "t")
    assert (t["lost"], t["dup"]) == (0, 1)


def test_t0_on_the_lem_store_drops_one_row_at_read(W):
    c = _lem_store_world(W, TRUTH3)
    W.mutation = "T0"
    try:
        t = c.tally("t", "t")
    finally:
        W.mutation = None
    assert (t["lost"], t["stored_effective"]) == (1, 2)


def test_t0_on_the_lem_store_drops_a_row_that_counts(W):
    # The first stored row is a hidden replay. Dropping IT changes no term of
    # the tally, so a T0 that took rows[0] would be a mutation that cannot
    # kill — T0 must remove a row the tally actually counts.
    c = _lem_store_world(W, [TRUTH3[0]] + TRUTH3, annotations=[(1, "replay_duplicate")])
    W.mutation = "T0"
    try:
        t = c.tally("t", "t")
    finally:
        W.mutation = None
    assert (t["lost"], t["stored_effective"]) == (1, 2)


def test_a_missing_lem_store_is_an_error_not_an_empty_store(W):
    import os
    import types
    c = W(road_modes={"A": "404", "B": "404"})
    c.emit(3)
    c.server._app = types.SimpleNamespace(config={"LEM_STORE": True})
    c.record_in = "lem"
    assert not os.path.exists(os.environ["LEM_STORE_PATH"])
    with pytest.raises(RuntimeError, match="missing store is not an empty one"):
        c.tally("t", "t")
