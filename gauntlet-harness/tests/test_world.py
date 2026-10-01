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
    assert (t["lost"], t["dup"], t["log_lost"], t["log_dup"]) == (0, 0, 0, 0)
    assert t["truth_prints"] == t["stored_effective"] == 9


def test_an_armed_kill_that_is_never_reached_is_an_error(W, loaded):
    from gharness.world import KillNeverReached
    c = W()
    c.emit(3)
    c.kill_at("after_journal_before_cursor")
    c.poll()
    with pytest.raises(KillNeverReached) as e:
        c.assert_killed("after_journal_before_cursor")
    # this worktree HAS the plumbing (the call sites come with P1)
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
    assert (t["lost"], t["stored_effective"], t["log_lost"]) == (1, 2, 1)


def test_unmeasurable_counters_are_none_not_zero(W):
    c = W()
    c.emit(1); c.poll()
    t = c.tally("t", "t")
    # v3.9-shaped server: no LEM store, no expect, no v2 sync
    assert t["conflicts"] is None
    assert t["expect_audit_hits"] is None
    assert t["records_resent"] is None
