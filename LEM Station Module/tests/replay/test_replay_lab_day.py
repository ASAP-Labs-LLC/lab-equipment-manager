"""Replay of a lab day against the REAL modules and a REAL sqlite LabCore.

THE BAR (hardened 2026-08-11, after a critique that broke the first version)
────────────────────────────────────────────────────────────────────────────
A reading has landed only when ALL THREE hold:

  1. IDENTITY IS INTACT.  `samples` contains exactly the identities the LIMS
     logged in — no phantom minted from whatever the instrument printed — and
     `sample_tests` contains no row for a sample that does not exist.
  2. IT IS ON THE RIGHT TEST.  No value is written under a test the bench did
     not measure.
  3. IT IS READABLE UNDER THE LIMS'S OWN LAB ID.  The Results grid's own inner
     join, run with the module's real date filter, returns it for `lab_id ==
     the canonical ID` — exact, never a suffix resemblance.

The first version of this file asked only (3), and asked it with suffix
matching. That certified as "delivered" a fix which mints a phantom sample
"34566" next to the LIMS's "081126-34566": the reading is readable, the LIMS
record's density is blank forever, and the phantom is invisible under the
shipped date filter. It scored that fix 19/20. Requirements (1) and (2) exist
because of that.

WHAT A NOT-YET-LOGGED SAMPLE IS ALLOWED TO DO
────────────────────────────────────────────
Several events print a Lab ID LabCore has never heard of. Demanding immediate
delivery there is unsatisfiable without inventing a sample — so those events
now replay the whole arc: the print arrives, the LIMS catches up, the bench
polls again, and the reading must be readable under the LIMS's ID. That is
satisfiable by holding-and-retrying and by resolving identity at write time,
and it is not satisfiable by dropping the reading or by minting a sample.

CONTROLS
────────
  C1 controls LabStation only (its dirty-branch), and says so.
  C2 controls the LEM road end to end, including that the number reaches the
     grid the operator is looking at — the thing nothing used to assert, which
     is why deleting the whole Results hand-off used to cost one test.
"""
from datetime import timedelta

import pytest

import conftest
import lab_day
import replay_paths

pytestmark = pytest.mark.replay

if replay_paths.missing_reason():
    pytest.skip(replay_paths.missing_reason(), allow_module_level=True)


# ── the day's vocabulary ─────────────────────────────────────────────────────
DENSITY = "D4052 Density"
API = "D287 API Gravity"
SULFUR = "D5453 Sulfur"
FLASH = "D93 Flash Point"
VISC = "D445 Viscosity"
IBP = "D86 IBP"
FBP = "D86 FBP"

# The pivot. The instrument prints the bare number on the sample cup; LabCore's
# sample was created by the LIMS under the dated form.
BARE = "34566"
# Dated from the replay's own day, not written down — see conftest.DAY. The
# prefix has to track the calendar because the station's shipped "Yesterday"
# filter is built from the real clock, so a literal here would put the sample
# outside the range on any day but the one this was typed on.
CANONICAL = f"{conftest.DAY:%m%d%y}-34566"
# A sample from the day before carrying the same cup number.
#
# THIS CANNOT HAPPEN IN THE LAB, and the harness used to assert that it could.
# Ryan: "This can never happen because its linear from 0 to indef. But if it
# does choose the closer date." The numeric Lab ID is one monotonic, never-reused
# sequence over the whole life of the lab, so 34566 is issued exactly once and
# the dated prefix is a label on a unique number, not a per-day cup number.
#
# It is kept for two jobs, both defensive. The oracle self-test below uses it to
# prove the oracle will not let a value on one sample answer for another, which
# is about the ORACLE and needs two samples that resemble each other however
# they got there. And the collision test proves what the bench does when the
# data is wrong anyway: it files on the nearer date rather than stopping.
YESTERDAYS = f"{conftest.DAY - timedelta(days=1):%m%d%y}-34566"


# ─────────────────────────────────────────────────────────────────────────────
# the bar, as code
# ─────────────────────────────────────────────────────────────────────────────

def explain(oracle, gateway, test=None, lab_id=None):
    """Why a reading is not readable — printed into every assertion message so
    a failure names the mechanism instead of just a None."""
    return (
        "\n  samples the LIMS logged : {seeded}"
        "\n  samples actually present: {present}"
        "\n  PHANTOM samples (minted by the software): {phantom}"
        "\n  ORPHAN sample_tests rows (no sample): {orphans}"
        "\n  sample_tests rows: {raw}"
        "\n  update_cell ops emitted: {ops}"
        "\n  insert_sample ops emitted: {ins}"
    ).format(seeded=sorted(oracle.seeded_samples), present=sorted(oracle.samples()),
             phantom=oracle.phantom_samples(), orphans=oracle.orphan_rows(),
             raw=oracle.raw_test_rows(), ops=gateway.value_writes(),
             ins=gateway.inserted_samples())


def assert_identity_intact(oracle, gateway):
    """Requirement 1. The LIMS owns sample identity.

    A bench instrument printing a bare cup number may not mint a sample: the
    phantom never matches the LIMS record, the LIMS record's cell stays blank
    forever, and under the shipped "Yesterday" filter the phantom — stamped
    `datetime.now()` by insert_sample (LabCore.py:7558) — is not even visible.
    An orphan `sample_tests` row is the same failure seen from the other side:
    the grid's INNER JOIN can never return it.
    """
    phantom = oracle.phantom_samples()
    assert not phantom, (
        f"{len(phantom)} sample identities were minted by the software, not "
        f"logged in by the lab: {phantom}. The LIMS's own record keeps a blank "
        "cell and nobody is told." + explain(oracle, gateway))
    orphans = oracle.orphan_rows()
    assert not orphans, (
        f"{len(orphans)} result rows were written for samples that do not "
        f"exist: {orphans}. LabCore accepts them (no foreign key) and the "
        "grid's inner join can never return them."
        + explain(oracle, gateway))


def assert_right_test(gateway, measured):
    """Requirement 2. A value filed under a test nobody ran is worse than a
    lost value: it is readable, it is green, and it is wrong."""
    measured = set(measured)
    wrong = sorted({t for _lab, t, _v in gateway.value_writes()} - measured)
    assert not wrong, (
        f"values were written under tests the bench never measured: {wrong}; "
        f"it measured {sorted(measured)}. _append_lab_id_row maps an uncached "
        "Lab ID to the column's FIRST watched test (LabStation.pyw:13070).")


def assert_landed(oracle, gateway, *, test, lab_id, value, measured=None,
                  date_sql="", date_params=(), why=""):
    """All three requirements, in the order a lab cares about them."""
    assert_identity_intact(oracle, gateway)
    assert_right_test(gateway, measured or [test])
    got = oracle.delivered(test, lab_id, date_sql=date_sql,
                           date_params=date_params)
    assert got == value, (
        (why or "the reading is not readable for the sample the lab logged in.")
        + explain(oracle, gateway, test, lab_id))


def run_bench(bench, machine):
    """One poll of the instrument, start to finish."""
    bench.set_machine(machine, publish=False)
    lab_day.poll(bench)


def lims_logs(seed, results=None, *, samples=(), tests=(), received=None):
    """The LIMS catching up: the sample is logged in, its tests assigned, and
    the station's grid reloads the way it does when the operator refreshes."""
    seed(samples=samples, tests=tests, received=received)
    if results is not None:
        lab_day.load_grid(results)


# ─────────────────────────────────────────────────────────────────────────────
# smoke: the harness really is driving the real things, the real way
# ─────────────────────────────────────────────────────────────────────────────

def test_harness_drives_the_real_modules(labstation, labcore, bench, lem,
                                         make_results, gateway, db):
    results = make_results([("Density", [DENSITY])])
    assert type(results).__name__ == "ResultsModule"
    assert type(results).__module__.startswith("labstation")
    assert labcore._BATCH_INNER_OPS["update_cell"] is labcore._batch_update_cell
    assert bench.module_type == "LEMStation"
    assert results.module_id in bench.context.modules
    assert bench.module_id in bench.context.modules
    assert str(db).endswith(".sqlite")


def test_both_modules_get_the_same_threading_split(lem, labstation):
    """LEM's worker hop must be the SAME deferred hop LabStation gets.

    An inline `cb(fn())` collapses `_process_outcome` (worker: decides
    sync_rows) and `_show_outcome` (main thread: does the Results hand-off)
    into one call stack, so a fix whose correctness depends on that ordering is
    judged under an ordering production cannot produce.
    """
    assert lem._run_in_thread is lab_day.deferred_run_in_thread
    assert labstation._run_in_thread is lab_day.deferred_run_in_thread


def test_clock_is_the_only_now(clock, bench, lem):
    from datetime import datetime
    assert lem.datetime.now() == clock.now()
    clock.advance(hours=3)
    assert lem.datetime.now() == clock.now()
    assert isinstance(lem.datetime.now(), datetime)


def test_oracle_refuses_a_suffix_resemblance(seed, oracle, gateway, labcore, db):
    """The oracle itself, under test.

    Two samples that resemble each other, one holding a value. It must never
    answer for the other — the previous oracle's suffix rule said it did, so a
    fix that filed today's density onto another day's sample scored as correct
    delivery. This is a statement about the ORACLE, not about lab traffic: see
    YESTERDAYS for why the lab cannot produce this pair.
    """
    seed(samples=[YESTERDAYS, CANONICAL],
         tests=[(YESTERDAYS, DENSITY), (CANONICAL, DENSITY)])
    gateway.write("update_cell", {"lab_id": YESTERDAYS, "test_name": DENSITY,
                                  "value": "0.8654"})
    assert oracle.delivered(DENSITY, YESTERDAYS) == "0.8654"
    assert oracle.delivered(DENSITY, CANONICAL) is None, (
        "today's sample has no density; only yesterday's does."
        + explain(oracle, gateway))
    assert oracle.phantom_samples() == []


def test_oracle_names_a_minted_sample_a_phantom(seed, oracle, gateway):
    """The audit that makes requirement 1 enforceable."""
    seed(samples=[CANONICAL], tests=[(CANONICAL, DENSITY)])
    gateway.write("batch", {"operations": [
        {"operation": "insert_sample", "params": {"lab_id": BARE}},
        {"operation": "update_cell", "params": {
            "lab_id": BARE, "test_name": DENSITY, "value": "0.8654"}}]})
    assert oracle.phantom_samples() == [BARE]
    assert oracle.delivered(DENSITY, CANONICAL) is None


# ─────────────────────────────────────────────────────────────────────────────
# C1 — CONTROL (LabStation only): the 1 AM refresh while _grid_dirty is True
# ─────────────────────────────────────────────────────────────────────────────

def test_C1_control_labstation_dirty_branch_keeps_an_unsaved_edit(
        make_results, seed, oracle, gateway, clock):
    """This controls LABSTATION, not LEM — it never constructs the LEM module.

    Kept because it is the evidence for brief point 11 (the 1 AM repaint is not
    what destroys a reading), and labelled so nobody mistakes it for a guard
    against a LEM regression. C2 is that guard.
    """
    seed(samples=[CANONICAL], tests=[(CANONICAL, DENSITY)])
    results = make_results([("Density", [DENSITY])])
    lab_day.load_grid(results)
    # The product itself must notice the edit — the harness no longer sets
    # _grid_dirty by hand.
    assert lab_day.type_into_grid(results, CANONICAL, 0, "0.8654"), \
        "LabStation did not notice the operator's edit"

    lab_day.daily_refresh(results, clock)
    results._flush_write_queue()
    lab_day.settle()

    assert_landed(
        oracle, gateway, test=DENSITY, lab_id=CANONICAL, value="0.8654",
        why="CONTROL — sound today: _refresh_grid's dirty branch "
            "(LabStation.pyw:11363-11372) snapshots the pending edit, merges "
            "it into test_index and enqueues the write before repainting.")


# ─────────────────────────────────────────────────────────────────────────────
# C2 — CONTROL (LEM end to end): the reading reaches LabCore AND the screen
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("printed", [CANONICAL, BARE],
                         ids=["prints-the-canonical-id", "prints-the-bare-id"])
def test_C2_control_reading_reaches_labcore_and_the_operators_screen(
        printed, bench, lem, make_results, fresh_view, printer, seed, oracle,
        gateway):
    """The whole road, working: a logged-in sample with the test assigned and a
    column watching it.

    Asserts the three things the first harness never did:
      • the number is ON THE GRID the operator is watching (`on_screen`);
      • it is in LabCore under the LIMS's identity;
      • a BRAND-NEW Results module, which has only ever seen LabCore, paints it
        — i.e. it does not live in a widget.
    """
    seed(samples=[CANONICAL], tests=[(CANONICAL, DENSITY)])
    results = make_results([("Density", [DENSITY])])
    lab_day.load_grid(results)

    machine = lab_day.make_machine(lem, csv_path=printer.path,
                                   mappings=[(1, [DENSITY])])
    printer.prints(f"{printed},0.8654")
    run_bench(bench, machine)
    lab_day.push_now(results)

    assert lab_day.on_screen(results, CANONICAL, "Density") == "0.8654", (
        "the operator standing at the bench cannot see the number the "
        "instrument just printed on the row for their sample."
        + explain(oracle, gateway))
    assert_landed(oracle, gateway, test=DENSITY, lab_id=CANONICAL,
                  value="0.8654",
                  why="CONTROL — the plain working path must stay working.")
    fresh = fresh_view([("Density", [DENSITY])])
    assert lab_day.on_screen(fresh, CANONICAL, "Density") == "0.8654", (
        "a Results module that has only ever read LabCore cannot show the "
        "reading — it lived in a widget." + explain(oracle, gateway))


# ─────────────────────────────────────────────────────────────────────────────
# E1 — the print arrives before the LIMS has logged the sample
# ─────────────────────────────────────────────────────────────────────────────

def test_E1_print_arriving_before_the_sample_is_logged(bench, lem, make_results,
                                                       printer, seed, oracle,
                                                       gateway):
    """The bench runs the cup, the paperwork lands an hour later.

    Immediate delivery is impossible here and the harness does not ask for it:
    it asks that nothing is minted and nothing is stranded, and that once the
    LIMS logs the sample the reading is readable under the LIMS's Lab ID.
    Holding-and-retrying satisfies this; so does resolving identity at write
    time. Dropping the reading does not, and neither does inventing a sample.
    """
    results = make_results([("Density", [DENSITY])])
    lab_day.load_grid(results)
    assert results._grid_filtered.rowCount() == 0, "grid starts empty"

    machine = lab_day.make_machine(lem, csv_path=printer.path,
                                   mappings=[(1, [DENSITY])])
    printer.prints(f"{BARE},0.8654")
    run_bench(bench, machine)
    lab_day.push_now(results)

    # Nothing may be minted, and nothing may be written where no reader can go.
    assert_identity_intact(oracle, gateway)

    # The LIMS catches up, and the bench keeps polling as it always does.
    lims_logs(seed, results, samples=[CANONICAL],
              tests=[(CANONICAL, DENSITY)])
    lab_day.poll(bench)
    lab_day.push_now(results)

    assert_landed(
        oracle, gateway, test=DENSITY, lab_id=CANONICAL, value="0.8654",
        why="the sample is now logged in as 081126-34566 and the bench has "
            "polled again, but the reading it took an hour ago is gone. "
            "_check_test_assignments (LabStation.pyw:12691) is the only code "
            "that adopts a canonical Lab ID and the LEM path never calls it.")


# ─────────────────────────────────────────────────────────────────────────────
# E2 — bare printed ID vs canonical dated sample; the painted row is the pivot
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("canonical_row_painted", [True, False],
                         ids=["canonical-row-painted", "no-row-painted"])
def test_E2_bare_id_against_canonical_sample(canonical_row_painted, bench, lem,
                                             make_results, printer, seed,
                                             oracle, gateway):
    """Same print, same sample in LabCore. One variable: whether the row was
    already on the grid. That is the whole difference between a stored result
    and a lost one, because `_lab_id_suffix` laundering only runs on a PAINTED
    row."""
    seed(samples=[CANONICAL],
         tests=[(CANONICAL, DENSITY)] if canonical_row_painted else [])
    results = make_results([("Density", [DENSITY])])
    lab_day.load_grid(results)
    assert (results._grid_filtered.rowCount() == 1) == canonical_row_painted

    machine = lab_day.make_machine(lem, csv_path=printer.path,
                                   mappings=[(1, [DENSITY])])
    printer.prints(f"{BARE},0.8654")
    run_bench(bench, machine)
    lab_day.push_now(results)

    assert_landed(
        oracle, gateway, test=DENSITY, lab_id=CANONICAL, value="0.8654",
        why="the instrument printed '34566'; the lab's sample is "
            "'081126-34566'. Resolving that is LabCore's job, not a widget's.")


# ─────────────────────────────────────────────────────────────────────────────
# E2b — the collision that cannot happen: it files on the nearer date
# ─────────────────────────────────────────────────────────────────────────────

def test_E2b_a_cup_number_answering_to_two_samples_files_on_the_nearer_date(
        bench, lem, make_results, printer, seed, oracle, gateway):
    """A defect in the data, not a Tuesday.

    Lab IDs are one monotonic, never-reused sequence, so 34566 is issued once
    and this pair cannot both exist — Ryan: "This can never happen because its
    linear from 0 to indef. But if it does choose the closer date."

    The bench must therefore not stop when it does happen. It resolves against
    the print's OWN date (the clock is 11 August; the print is today's), files
    on the nearer sample, and leaves the other alone. Refusing here would be a
    bench that has stopped filing because of a login it cannot fix, and the
    advice that used to go with the refusal — rename or close one in LabCore —
    would orphan every result already filed against it, since `sample_tests`
    has no foreign key onto `samples` and no cascade.
    """
    seed(samples=[YESTERDAYS, CANONICAL],
         tests=[(YESTERDAYS, DENSITY), (CANONICAL, DENSITY)])
    results = make_results([("Density", [DENSITY])])
    lab_day.load_grid(results)

    machine = lab_day.make_machine(lem, csv_path=printer.path,
                                   mappings=[(1, [DENSITY])])
    printer.prints(f"{BARE},0.8654")
    run_bench(bench, machine)
    lab_day.push_now(results)

    assert_landed(
        oracle, gateway, test=DENSITY, lab_id=CANONICAL, value="0.8654",
        why="the print was taken today, so today's sample is the nearer one.")
    assert oracle.delivered(DENSITY, YESTERDAYS) is None, (
        "and the other sample must be left exactly as it was."
        + explain(oracle, gateway))


# ─────────────────────────────────────────────────────────────────────────────
# E2c — the bench that has already run the broken code (every bench in the lab)
# ─────────────────────────────────────────────────────────────────────────────

def test_E2c_a_phantom_left_by_the_old_code_does_not_take_the_reading(
        bench, lem, make_results, printer, seed, oracle, gateway):
    """Not minting a phantom is only half the job.

    The pristine code emitted `insert_sample` under whatever the instrument
    printed, on every poll, on every bench — so `samples` ALREADY holds a bare
    "34566" beside the LIMS's "081126-34566" for every cup this software has
    processed. Tier order alone hands the reading straight back to that phantom,
    the LIMS's own cell stays blank, and the fix ships correct and inert.

    Both identities are seeded here because that is the state of the installed
    base, not because the LIMS created both: the audit is not what is under test
    on this event, the choice between them is. Nothing is renamed or deleted to
    win it — renaming orphans every result already filed against the phantom,
    since `sample_tests` has no foreign key onto `samples` and no cascade.
    """
    seed(samples=[BARE, CANONICAL],
         tests=[(BARE, DENSITY), (CANONICAL, DENSITY)])
    results = make_results([("Density", [DENSITY])])
    lab_day.load_grid(results)

    machine = lab_day.make_machine(lem, csv_path=printer.path,
                                   mappings=[(1, [DENSITY])])
    printer.prints(f"{BARE},0.8654")
    run_bench(bench, machine)
    lab_day.push_now(results)

    assert_landed(
        oracle, gateway, test=DENSITY, lab_id=CANONICAL, value="0.8654",
        why="the sample the LIMS logged in still has a blank density, and the "
            "reading went to the sample this software minted for itself years "
            "ago — which is the bug, exactly as it was.")
    assert oracle.delivered(DENSITY, BARE) is None, (
        "and nothing new may be written onto the phantom either."
        + explain(oracle, gateway))
    assert BARE in oracle.samples(), (
        "the phantom is left exactly where it is: renaming or deleting it "
        "orphans every result already filed against it.")


# ─────────────────────────────────────────────────────────────────────────────
# E3 — delivery must not depend on who happens to be watching
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("watcher", ["none-on-canvas", "watches-something-else",
                                     "watches-the-method"])
def test_E3_delivery_must_not_depend_on_who_is_watching(watcher, bench, lem,
                                                        make_results, printer,
                                                        seed, oracle, gateway):
    """One database, one print, one sample. The only variable is what a UI on
    the canvas happens to be watching.

    (This absorbs the old E14 "no Results module" control, which the critique
    correctly called a duplicate of the watches-nothing arm — and which used to
    ASSERT that LEM minted a sample under the bare printed ID, i.e. it required
    the behaviour that forks the sample table.)
    """
    seed(samples=[CANONICAL], tests=[(CANONICAL, DENSITY)])
    results = None
    if watcher != "none-on-canvas":
        watched = DENSITY if watcher == "watches-the-method" else SULFUR
        results = make_results([("Watched", [watched])])
        lab_day.load_grid(results)

    machine = lab_day.make_machine(lem, csv_path=printer.path,
                                   mappings=[(1, [DENSITY])])
    printer.prints(f"{BARE},0.8654")
    run_bench(bench, machine)
    if results is not None:
        lab_day.push_now(results)

    assert_landed(
        oracle, gateway, test=DENSITY, lab_id=CANONICAL, value="0.8654",
        why="a Results column watching the method is what empties sync_rows "
            "(lem_station_module.py:3001-3003) and so disables the only "
            "insert_sample the LEM path ever emits (:1625); with nothing "
            "watching, that batch files everything under the BARE printed ID. "
            "Neither road resolves the sample's real identity.")


# ─────────────────────────────────────────────────────────────────────────────
# E4 — a column watching MULTIPLE tests must not rename the measurement
# ─────────────────────────────────────────────────────────────────────────────

def test_E4_multi_test_column_must_not_file_fbp_as_ibp(bench, lem, make_results,
                                                       printer, seed, oracle,
                                                       gateway):
    """The most dangerous failure in the set: not a lost value, a WRONG one.

    A "Distillation" column watches IBP and FBP. The bench measured FBP. The
    Lab ID is not on the grid yet, so `_append_lab_id_row` maps it to the
    column's FIRST watched test and a final boiling point is filed as an
    initial one — readable, green, and wrong.
    """
    seed(samples=[CANONICAL])          # sample exists; no tests assigned yet
    results = make_results([("Distillation", [IBP, FBP])])
    lab_day.load_grid(results)
    assert results._grid_filtered.rowCount() == 0, "no row for it yet"

    machine = lab_day.make_machine(lem, csv_path=printer.path,
                                   mappings=[(1, [FBP])])
    printer.prints(f"{BARE},371.2")
    run_bench(bench, machine)
    lab_day.push_now(results)

    # Named first, because a wrong value is this test's subject: it is readable,
    # it is green, and it is a different measurement.
    assert_right_test(gateway, [FBP])
    assert_landed(
        oracle, gateway, test=FBP, lab_id=CANONICAL, value="371.2",
        measured=[FBP],
        why="371.2 is a final boiling point. Anything that stores it as an "
            "initial one is a wrong result, not a missing one.")


# ─────────────────────────────────────────────────────────────────────────────
# E5 — the target cell is blacked out (sample exists, test not assigned)
# ─────────────────────────────────────────────────────────────────────────────

def test_E5_reading_for_an_unassigned_test_is_still_recorded(bench, lem,
                                                             make_results,
                                                             printer, seed,
                                                             oracle, gateway):
    """The sample is real and on the grid (it has Sulfur), but Density was
    never assigned to it, so the Density cell paints "not applicable".

    LabCore will happily create the missing `sample_tests` row under the real
    sample (`_batch_update_cell`, LabCore.py:7519-7549), so this IS storable
    without inventing anything — the reading is dropped purely because a widget
    painted the cell grey (lem:3151-3153) and the hand-off still reported
    success (:3160).
    """
    seed(samples=[CANONICAL], tests=[(CANONICAL, SULFUR)])
    results = make_results([("Density", [DENSITY]), ("Sulfur", [SULFUR])])
    lab_day.load_grid(results)
    snapshot = lab_day.grid_snapshot(results)
    assert snapshot, "the sample should be painted"
    assert snapshot[0][2][0][2] is True, \
        f"precondition: Density cell should be blacked out: {snapshot}"

    machine = lab_day.make_machine(lem, csv_path=printer.path,
                                   mappings=[(1, [DENSITY])])
    printer.prints(f"{BARE},0.8654")
    run_bench(bench, machine)
    lab_day.push_now(results)

    assert_landed(
        oracle, gateway, test=DENSITY, lab_id=CANONICAL, value="0.8654",
        why="the instrument measured a density for a sample whose density was "
            "never assigned. A grey cell is a UI statement about a work order, "
            "not permission to discard a measurement.")


# ─────────────────────────────────────────────────────────────────────────────
# E6 — the target cell already holds a value (never-clobber)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("first_value_from", ["operator", "an-earlier-run"])
@pytest.mark.parametrize("after_the_nightly_repaint", [False, True],
                         ids=["same-session", "after-the-1am-repaint"])
def test_E6_a_second_reading_is_not_resolved_by_deletion(
        first_value_from, after_the_nightly_repaint, bench, lem, make_results,
        printer, seed, oracle, gateway, clock):
    """A value is already in the cell and the instrument prints a different one.

    `_fill_results_grids` refuses to clobber (lem:3154-3155) but still returns
    True, and `sync_rows` was already emptied — so the newer reading is
    resolved by deletion.

    Two arms per source of the first value:
      • an EARLIER RUN — the newer measurement supersedes it and must become
        the stored result;
      • the OPERATOR — whether the instrument should win is a policy call this
        harness does not make, so it asks only that the disagreement is
        RECORDED against the same sample identity, which is strictly weaker
        than "0.8654 appears somewhere" (the old assertion could not tell a
        recorded disagreement from a value filed on a phantom).

    `after_the_nightly_repaint` is a PARAMETER, not a separate event: the
    critique proved the 1 AM repaint has no causal role here (both arms fail
    for the never-clobber reason), and brief point 11 already refuted the
    repaint theory. It is replayed so the claim stays checked, not asserted as
    a mechanism.
    """
    seed(samples=[CANONICAL], tests=[(CANONICAL, DENSITY)])
    results = make_results([("Density", [DENSITY])])
    lab_day.load_grid(results)

    machine = lab_day.make_machine(lem, csv_path=printer.path,
                                   mappings=[(1, [DENSITY])])
    if first_value_from == "operator":
        assert lab_day.type_into_grid(results, CANONICAL, 0, "0.9000")
        lab_day.push_now(results)
    else:
        printer.prints(f"{BARE},0.9000")
        run_bench(bench, machine)
        lab_day.push_now(results)
    assert oracle.delivered(DENSITY, CANONICAL) == "0.9000", \
        "precondition: the first value must be stored" + explain(oracle, gateway)

    if after_the_nightly_repaint:
        lab_day.daily_refresh(results, clock)

    clock.advance(hours=1)
    printer.prints(f"{BARE},0.8654")
    run_bench(bench, machine)
    lab_day.push_now(results)

    assert_identity_intact(oracle, gateway)
    assert_right_test(gateway, [DENSITY])
    if first_value_from == "an-earlier-run":
        got = oracle.delivered(DENSITY, CANONICAL)
        assert got == "0.8654", (
            "the bench re-ran the sample and measured 0.8654; the grid still "
            "reports the earlier 0.9000. A re-run supersedes."
            + explain(oracle, gateway))
    else:
        recorded = [w for w in gateway.value_writes(lab_id=CANONICAL,
                                                    test=DENSITY)
                    if w[2] == "0.8654"]
        assert recorded, (
            "the instrument disagreed with what the operator typed and the "
            "instrument's reading was never written against this sample at "
            "all. A disagreement must not be resolved by deletion."
            + explain(oracle, gateway))


# ─────────────────────────────────────────────────────────────────────────────
# E7 — five parsed methods, one watched column
# ─────────────────────────────────────────────────────────────────────────────

FIVE = [DENSITY, API, SULFUR, FLASH, VISC]
FIVE_VALUES = ["0.8654", "31.2", "12.4", "62.0", "3.15"]


def test_E7_all_five_parsed_methods_are_readable(bench, lem, make_results,
                                                 printer, seed, oracle,
                                                 gateway):
    """`sync_rows` is emptied for ALL rows once ANY column matches ANY method
    (lem:3001-3003), but only methods a column watches are delivered
    (:3113-3115). The other four are written by neither road."""
    seed(samples=[CANONICAL], tests=[(CANONICAL, m) for m in FIVE])
    results = make_results([("Density", [DENSITY])])   # watches ONE of the five
    lab_day.load_grid(results)

    machine = lab_day.make_machine(
        lem, csv_path=printer.path,
        mappings=[(i + 1, [m]) for i, m in enumerate(FIVE)])
    printer.prints(f"{BARE}," + ",".join(FIVE_VALUES))
    run_bench(bench, machine)
    lab_day.push_now(results)

    assert_identity_intact(oracle, gateway)
    assert_right_test(gateway, FIVE)
    expected = dict(zip(FIVE, FIVE_VALUES))
    missing = {m: v for m, v in expected.items()
               if oracle.delivered(m, CANONICAL) != v}
    assert not missing, (
        f"{len(missing)} of 5 parsed methods are unreadable: {sorted(missing)}."
        + explain(oracle, gateway))


# ─────────────────────────────────────────────────────────────────────────────
# E8 — durability: the reading must live in LabCore, not in a widget
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("widget_lost_by", ["the-1am-repaint", "a-restart"])
def test_E8_reading_outlives_the_widget_that_showed_it(widget_lost_by, bench,
                                                       lem, make_results,
                                                       fresh_view, printer,
                                                       seed, oracle, gateway,
                                                       clock):
    """Asked THROUGH the real module, not through sqlite.

    The old E8/E11 pair asserted a raw sqlite query that neither the repaint
    nor the restart could possibly influence — the critique proved both failed
    identically with the refresh disabled, i.e. they were E1 with decoration.
    This one asks the question that IS coupled to the event: the operator can
    read the number now; after the widget that held it is gone, can a module
    that has only ever seen LabCore still paint it? The precondition
    (`on_screen` before) and the assertion (`on_screen` on a fresh module
    after) are the same question asked of the same kind of object.
    """
    seed(samples=[CANONICAL], tests=[(CANONICAL, DENSITY)])
    results = make_results([("Density", [DENSITY])])
    lab_day.load_grid(results)

    machine = lab_day.make_machine(lem, csv_path=printer.path,
                                   mappings=[(1, [DENSITY])])
    printer.prints(f"{BARE},0.8654")
    run_bench(bench, machine)
    lab_day.push_now(results)
    assert lab_day.on_screen(results, CANONICAL, "Density") == "0.8654", (
        "precondition: the operator could read it before the widget went away."
        + explain(oracle, gateway))

    if widget_lost_by == "the-1am-repaint":
        before = lab_day.cell_identity(results, CANONICAL, "Density")
        lab_day.daily_refresh(results, clock)
        # Proof the event did something: the cell the operator is reading is a
        # DIFFERENT widget now, rebuilt from LabCore. Without this the arm
        # would pass on a grid the refresh never touched — the vacuity the
        # critique found in the old E8/E11 pair.
        assert lab_day.cell_identity(results, CANONICAL, "Density") != before, (
            "the nightly repaint did not rebuild the row, so this arm would "
            "not be testing the repaint at all.")
        survivor = results
    else:
        clock.advance(hours=20)
        survivor = fresh_view([("Density", [DENSITY])])

    assert lab_day.on_screen(survivor, CANONICAL, "Density") == "0.8654", (
        f"after {widget_lost_by} the number is no longer on any grid the "
        "operator can open — it was only ever in a widget."
        + explain(oracle, gateway))
    assert_landed(oracle, gateway, test=DENSITY, lab_id=CANONICAL,
                  value="0.8654")


# ─────────────────────────────────────────────────────────────────────────────
# E12 — reconciling must happen ONCE, not on every poll forever
# ─────────────────────────────────────────────────────────────────────────────

def test_E12_reconciling_a_late_sample_does_not_rewrite_forever(bench, lem,
                                                                make_results,
                                                                printer, seed,
                                                                oracle, gateway,
                                                                clock):
    """E1's arc, plus the cost of it.

    A held reading that is re-sent on every poll would keep a bench writing the
    same value all afternoon into a queue that refuses past 100 pending
    (MEMORY: labcore-write-queue-limits). So: after the LIMS catches up and the
    bench polls three more times, the reading is readable AND no
    (lab_id, test, value) was written twice.
    """
    results = make_results([("Density", [DENSITY])])
    lab_day.load_grid(results)
    machine = lab_day.make_machine(lem, csv_path=printer.path,
                                   mappings=[(1, [DENSITY])])
    printer.prints(f"{BARE},0.8654")
    run_bench(bench, machine)
    lab_day.push_now(results)

    lims_logs(seed, results, samples=[CANONICAL],
              tests=[(CANONICAL, DENSITY)])
    for _ in range(3):
        clock.advance(minutes=5)
        lab_day.poll(bench)
        lab_day.push_now(results)

    assert_landed(oracle, gateway, test=DENSITY, lab_id=CANONICAL,
                  value="0.8654",
                  why="the sample was logged in and the bench polled three "
                      "more times; nothing reconciled the reading.")
    dupes = gateway.duplicate_value_writes()
    assert not dupes, (
        f"the same value was written again on a later poll: {dupes}. Every "
        "repeat re-stamps updated_at and the operator and costs a slot in a "
        "queue that refuses past 100 pending." + explain(oracle, gateway))


# ─────────────────────────────────────────────────────────────────────────────
# E13 — the same input offered twice
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("re_offer", ["nothing-new", "source-re-read"])
@pytest.mark.parametrize("with_results_module", [True, False],
                         ids=["results-on-canvas", "no-results-module"])
def test_E13_re_offering_the_same_print_does_not_rewrite_it(
        with_results_module, re_offer, bench, lem, make_results, printer, seed,
        oracle, gateway, clock):
    """Two ways the same reading comes round again:

      • `nothing-new` — the ordinary case, a poll on an unchanged file. This is
        the one the real lab produces every 12 seconds all day.
      • `source-re-read` — the instrument's own log was rewritten, or the
        watched path was re-pointed, so the file is read from the top again.

    The direct-batch road has no idempotency at all; the Results road is
    idempotent only by accident, because the appended widget row still happens
    to hold the text. Both are measured here.
    """
    seed(samples=[CANONICAL], tests=[(CANONICAL, DENSITY)])
    results = make_results([("Density", [DENSITY])]) if with_results_module \
        else None
    if results is not None:
        lab_day.load_grid(results)

    machine = lab_day.make_machine(lem, csv_path=printer.path,
                                   mappings=[(1, [DENSITY])])
    printer.prints(f"{BARE},0.8654")
    run_bench(bench, machine)
    if results is not None:
        lab_day.push_now(results)

    clock.advance(minutes=1)
    if re_offer == "source-re-read":
        lab_day.rewind(machine)
    lab_day.poll(bench)
    if results is not None:
        lab_day.push_now(results)

    assert_identity_intact(oracle, gateway)
    dupes = gateway.duplicate_value_writes()
    assert not dupes, (
        f"the reading was written a second time ({re_offer}): {dupes}."
        + explain(oracle, gateway))
    assert oracle.delivered(DENSITY, CANONICAL) == "0.8654", (
        "and it must still be readable afterwards." + explain(oracle, gateway))


# ─────────────────────────────────────────────────────────────────────────────
# E15 — LabCore refusing writes past 100 pending, on BOTH roads
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("with_results_module", [True, False],
                         ids=["results-on-canvas", "no-results-module"])
def test_E15_a_refused_write_is_retried(with_results_module, bench, lem,
                                        make_results, printer, seed, oracle,
                                        gateway, clock):
    """LabCore returns an error DICT, never an exception.

    Parametrised on the road because the two roads are not equally equipped:
    ResultsModule has a retry queue (`_write_queue` / `_persist_write_queue`);
    LEM has none, and `_ingest_single` has already advanced `last_position`, so
    the print is consumed. The old version tested only the road WITHOUT a
    Results module and drew a conclusion about the road the bug takes.
    """
    seed(samples=[CANONICAL], tests=[(CANONICAL, DENSITY)])
    results = make_results([("Density", [DENSITY])]) if with_results_module \
        else None
    if results is not None:
        lab_day.load_grid(results)

    machine = lab_day.make_machine(lem, csv_path=printer.path,
                                   mappings=[(1, [DENSITY])])
    gateway.set_pending(gateway.QUEUE_LIMIT)     # LabCore is saturated
    printer.prints(f"{BARE},0.8654")
    run_bench(bench, machine)
    if results is not None:
        lab_day.push_now(results)
    assert oracle.delivered(DENSITY, CANONICAL) is None, \
        "precondition: nothing got through while the queue was full"

    # The busy minute passes; the bench keeps polling as it always does.
    gateway.set_pending(0)
    clock.advance(minutes=1)
    lab_day.poll(bench)
    if results is not None:
        lab_day.push_now(results)

    assert_landed(
        oracle, gateway, test=DENSITY, lab_id=CANONICAL, value="0.8654",
        why="the refusal arrived as an error dict, the print had already been "
            "consumed off the file, and nothing re-offered it. A busy minute "
            "silently loses results.")


# ─────────────────────────────────────────────────────────────────────────────
# E16 — the SHIPPED date filter, not the one the harness finds convenient
# ─────────────────────────────────────────────────────────────────────────────

def test_E16_reading_is_visible_under_the_shipped_date_filter(bench, lem,
                                                              make_results,
                                                              printer, seed,
                                                              oracle, gateway,
                                                              clock):
    """Every other event pins the picker to "All" so a missing row can only be
    the identity bug. The station ships on "Yesterday" (LabStation.pyw:9305),
    so one event runs there.

    This is also where a minted sample gives itself away for a second reason:
    `insert_sample` stamps `received_at` with `datetime.now()`
    (LabCore.py:7558), so a phantom created at parse time is not in yesterday's
    range and the grid cannot paint it even for the bench that made it.
    """
    from datetime import timedelta
    yesterday = ((clock.now().date() - timedelta(days=1)).isoformat()
                 + " 16:20:00")
    seed(samples=[CANONICAL], tests=[(CANONICAL, DENSITY)], received=yesterday)
    results = make_results([("Density", [DENSITY])], date_filter="Yesterday")
    lab_day.load_grid(results)
    date_sql, date_params = lab_day.date_clause(results)
    assert date_sql, "precondition: the shipped filter must actually filter"

    machine = lab_day.make_machine(lem, csv_path=printer.path,
                                   mappings=[(1, [DENSITY])])
    printer.prints(f"{BARE},0.8654")
    run_bench(bench, machine)
    lab_day.push_now(results)

    assert_landed(
        oracle, gateway, test=DENSITY, lab_id=CANONICAL, value="0.8654",
        date_sql=date_sql, date_params=date_params,
        why="the sample was logged in yesterday afternoon and run this "
            "morning — the ordinary case — and the grid the lab actually "
            "ships cannot show the result.")


# ─────────────────────────────────────────────────────────────────────────────
# E17 — the REPORTED result is the corrected one
# ─────────────────────────────────────────────────────────────────────────────

def test_E17_the_stored_result_is_the_corrected_reading(bench, lem,
                                                        make_results, printer,
                                                        seed, oracle, gateway):
    """No replay ever set `machine.corrections`, so the correction boundary
    (`apply_row_corrections`, lem:1544) never ran and a fix that stored the RAW
    reading passed everything.

    PAC Flash 2's -3.0, the module's own worked example: ISO/IEC 17025:2017
    §7.8.2 — a reported result must be the measurement result, which means
    corrected.
    """
    seed(samples=[CANONICAL], tests=[(CANONICAL, FLASH)])
    results = make_results([("Flash", [FLASH])])
    lab_day.load_grid(results)

    machine = lab_day.make_machine(lem, csv_path=printer.path,
                                   mappings=[(1, [FLASH])],
                                   corrections={FLASH: -3.0})
    printer.prints(f"{BARE},62.0")
    run_bench(bench, machine)
    lab_day.push_now(results)

    assert_landed(
        oracle, gateway, test=FLASH, lab_id=CANONICAL, value="59.0",
        why="the instrument read 62.0 and this bench carries a -3.0 "
            "correction, so the reported flash point is 59.0.")
    raw_writes = [w for w in gateway.value_writes() if w[2] == "62.0"]
    assert not raw_writes, (
        f"the uncorrected reading was stored as a result: {raw_writes}."
        + explain(oracle, gateway))


# ─────────────────────────────────────────────────────────────────────────────
# E18 — the reading is still on screen after the grid repaints itself
# ─────────────────────────────────────────────────────────────────────────────

def test_E18_a_painted_reading_survives_a_repaint_with_no_refetch(
        bench, lem, make_results, printer, seed, oracle, gateway):
    """Reported from the floor 2026-08-14: a cell populates, and when the NEXT
    print parses the previous one disappears. LabCore had everything; only the
    screen lost it, and a restart cleared it.

    E8 already asks whether a reading outlives the widget that showed it, and
    passes — because the daily refresh nulls the fetch token, so the grid
    reloads from LabCore and the value comes back from the record. This is the
    other repaint: the one that rebuilds from cache WITHOUT re-fetching, which
    is what `_refresh_grid` does whenever the selection has not changed.

    The hand-off paints cells with signals blocked, so LabStation's
    `_on_grid_item_changed` never runs and the Results module's own `test_index`
    never learns the value — and `_cell_for` rebuilds every cell from exactly
    that. So the paint survived only until the next repaint. In the lab the
    trigger was the bench itself: `update_cell` is in `_LIVE_REFRESH_OPS`, so
    every reading LEM filed made the Results module re-read, and the previous
    reading vanished as the next one landed. It was invisible before this change
    because the old hand-off set `_grid_dirty`, and `_poll_live_changes` skips
    the refresh while that flag is up.
    """
    lab_id = f"{conftest.DAY:%m%d%y}-40003"
    seed(samples=[lab_id], tests=[(lab_id, DENSITY)])
    results = make_results([("Density", [DENSITY])])
    lab_day.load_grid(results)

    machine = lab_day.make_machine(lem, csv_path=printer.path,
                                   mappings=[(1, [DENSITY])])
    printer.prints("40003,0.8654")
    run_bench(bench, machine)
    assert lab_day.on_screen(results, lab_id, "Density") == "0.8654", (
        "the reading never reached the grid at all." + explain(oracle, gateway))

    # No token change, so no re-fetch: a pure cache repaint, which is what the
    # live-refresh poll, a column toggle and a tab switch all end in.
    results._refresh_grid()
    lab_day.settle()

    assert lab_day.on_screen(results, lab_id, "Density") == "0.8654", (
        "the reading vanished from the grid when it repainted, though LabCore "
        f"still holds it ({oracle.delivered(DENSITY, lab_id)!r}). The operator "
        "watches a result they just took disappear."
        + explain(oracle, gateway))


def test_E18b_the_previous_reading_is_still_there_when_the_next_one_lands(
        bench, lem, make_results, printer, seed, oracle, gateway):
    """The shape the floor actually reported, end to end."""
    first = f"{conftest.DAY:%m%d%y}-40001"
    second = f"{conftest.DAY:%m%d%y}-40002"
    seed(samples=[first, second],
         tests=[(first, DENSITY), (second, DENSITY)])
    results = make_results([("Density", [DENSITY])])
    lab_day.load_grid(results)

    machine = lab_day.make_machine(lem, csv_path=printer.path,
                                   mappings=[(1, [DENSITY])])
    printer.prints("40001,0.8654")
    run_bench(bench, machine)

    printer.prints("40002,0.9111")
    run_bench(bench, machine)
    results._refresh_grid()          # what the bench's own write triggers
    lab_day.settle()

    assert lab_day.on_screen(results, first, "Density") == "0.8654", (
        "the previous sample's reading was removed when the next one parsed."
        + explain(oracle, gateway))
    assert lab_day.on_screen(results, second, "Density") == "0.9111", (
        "the new reading is not on the grid." + explain(oracle, gateway))
