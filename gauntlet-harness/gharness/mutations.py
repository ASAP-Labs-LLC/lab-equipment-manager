"""The gate's self-test: break something on purpose; the gate must go red.

Spec §15.7. A mutation that leaves the gate green means the gate cannot see
that failure, and the gate is not trusted there. Two kinds:

* **harness** — applied inside the gate process. T0 deletes one stored row
  before the tally (a real DELETE on a LabCore store; on the append-only LEM
  store the row is dropped at read). It catches any tally term that was
  multiplied by 0 or zeroed by hand.

* **source** — a regex edit of a COPY of the target tree (the worktree is
  never touched), then the gate is run on the copy with `--code`. The regex
  must match at least once; one that matches nothing is UNAVAILABLE ("the code
  this mutation breaks does not exist on this target yet"), reported as such
  and never counted as killed. The piece that builds the code owns the pattern
  (`owner`) and fills it in when the code exists.

Verdicts: KILLED (some scenario green in the clean run is red under the
mutation), SURVIVED (none — the gate is blind there), UNAVAILABLE.
"""
import os
import re
import shutil

MUTATIONS = {
    "T0": {"kind": "harness", "what": "tally row deletion: one stored run row removed",
           "owner": "P0"},
    # ── today's mechanisms (owner P0): these match v3.9.0 AND the worktree,
    # so the source route — copy the tree, edit it, run the gate on the copy —
    # is exercised on every self-test, not only once v4 code exists. Each
    # breaks one thing v3.9 already does right; the gate must notice.
    "offset_not_advanced": {
        "kind": "source", "owner": "P0",
        "what": "single_csv byte offset never advances (every poll re-reads the file)",
        # v3.9 advances in `_read_tail`; v4 in SingleCsvSource._append (P2
        # moved it there — the alternation follows the code, and still matches
        # exactly once in each tree).
        "pattern": r"new_position = f\.tell\(\)|"
                   r"new_position = last_position \+ chunks\[-1\]\[1\]",
        "replace": "new_position = last_position"},
    "refusal_counted_as_filed": {
        "kind": "source", "owner": "P0",
        "what": "a busy refusal of a machine-log batch is counted as filed",
        "pattern": r"refused = refusal_reason\(result\)",
        "replace": "refused = None"},
    "requeue_on_raise_off": {
        "kind": "source", "owner": "P0",
        "what": "a machine-log batch whose write raised is dropped, not put back",
        "pattern": r"self\._pending_events\.extendleft\(reversed\(batch\)\)\n(\s*)raise",
        "replace": r"pass\n\1raise"},
    "cell_key_ignores_value": {
        "kind": "source", "owner": "P0",
        "what": "a result write is 'the same write' whatever its value",
        "pattern": r'str\(params\.get\("value"\) or ""\)\)',
        "replace": '"")'},
    # The store's uniqueness of (uid, epoch, seq), which it enforces TWICE:
    # the `ux_log_bench` unique index, and the (uid, epoch, seq) arm of the
    # `lem_log_no_overwrite` trigger (which refuses a colliding insert before
    # any index is consulted, OR IGNORE included). The first form of this
    # mutation edited the index only and survived every gate run: with the
    # trigger still there, no observable thing changed — an equivalent
    # mutant (test_mutations pins that). Both edits together remove the
    # constraint the name promises; T2L's second-writer replay must go red.
    "unique_seq_off": {
        "kind": "source", "owner": "P6",
        "what": "unique (uid, epoch, seq) removed from the store "
                "(the unique index AND the overwrite trigger's seq arm)",
        "edits": [
            {"pattern": r"CREATE UNIQUE INDEX (IF NOT EXISTS )?ux_log_bench",
             "replace": r"CREATE INDEX \1ux_log_bench"},
            {"pattern": r'" OR \(NEW\.bench_seq IS NOT NULL AND EXISTS',
             "replace": r'" OR (0 AND NEW.bench_seq IS NOT NULL AND EXISTS'}]},
    # The server's resend handling (§6.1 step 3, P7): a record at or below
    # the cursor's `acked` is skipped, and one already in bench_record is not
    # written again. Off, a re-sent record is inserted a second time; the
    # store's own guards then refuse the whole sync (a 503, forever), so the
    # bench never hears its records acked and never files its held results.
    # T2L — 240 held prints and lost answers, so hundreds of resends — must
    # go red, or the gate cannot see the server's de-duplication break.
    "resend_skip_off": {
        "kind": "source", "owner": "P7",
        "what": "the sync stores a re-sent record again (cursor skip and the "
                "bench_record existence check removed)",
        "edits": [
            {"pattern": r"if seq <= acked:\n(\s*)continue( +# a resend: already held)",
             "replace": r"if False:\n\1continue\2"},
            {"pattern": r'"kind, ts, body\) SELECT \?, \?, \?, \?, \?, \? WHERE NOT EXISTS "\n'
                        r'(\s*)"\(SELECT 1 FROM bench_record WHERE machine_uid = \? AND "\n'
                        r'(\s*)"bench_epoch = \? AND bench_seq = \?\)",',
             "replace": r'"kind, ts, body) SELECT ?, ?, ?, ?, ?, ? WHERE 1 OR NOT EXISTS "\n'
                        r'\1"(SELECT 1 FROM bench_record WHERE machine_uid = ? AND "\n'
                        r'\2"bench_epoch = ? AND bench_seq = ?)",'}]},
    # ── the bench journal (owner P1) ──
    # A serial frame is CONSUMED when the poll takes it off the reader. v4
    # journals it on the reader's thread first; reversed, the frame reaches
    # the poll before any journal holds it (v3.9's K8 window) and the kill
    # point K8r measures the residual at is never reached.
    "journal_order_reversed": {
        "kind": "source", "owner": "P1",
        "pattern": r"if self\._on_frame is not None and frame\.strip\(\):",
        "replace": "if False:",
        "what": "journal-before-consume order reversed: serial frames reach the poll before the journal"},
    # The poll's readings never journaled: nothing re-delivers a kill's
    # readings and nothing suppresses a restarted bench's re-read.
    "journal_poll_off": {
        "kind": "source", "owner": "P1",
        "pattern": r"journaled = journal is not None and self\._journal_poll\(",
        "replace": "journaled = False and self._journal_poll(",
        "what": "a poll's readings are not journaled before they go to LabCore"},
    # ── the source readers (owner P2) ──
    # Both lines of replay defence at once: the cursor a restarted bench loads
    # (`cur = sources.get(self.key)` in SingleCsvSource.load) becomes None, so
    # it reads its file from the top, AND the journal's store check
    # (`journal.known(pk)` in _journal_intake) never recognises a key, so the
    # re-read is logged again. One replacement serves both sites.
    "cursor_and_keys_off": {
        "kind": "source", "owner": "P2",
        "pattern": r"sources\.get\(self\.key\)|journal\.known\(pk\)",
        "replace": "None",
        "what": "persisted cursor and content keys ignored"},
    # The one call site of the ordered diff, swapped for the count rule it
    # replaced (kept in the module as `resolve_by_count` for exactly this).
    "count_matching": {
        "kind": "source", "owner": "P2",
        "pattern": r"resolution = resolve_rewrite\(",
        "replace": "resolution = resolve_by_count(",
        "what": "ordered diff replaced by count matching (X1/X2 must fail)"},
    # ── the guarded results road (owner P3) ──
    # The guard's one decision point: every cell is a plain write whatever
    # the guard read found in it — today's unguarded road (A1/A3 overwrite,
    # K4/N4 re-send).
    "guard_off": {
        "kind": "source", "owner": "P3",
        "pattern": r"verdict, expect = decide_cell\(cur_rows, value, led\)",
        "replace": 'verdict, expect = ("write", "")',
        "what": "results guard read skipped"},
    # `batch_outcome` reads each index's error out of an `ok` answer; off,
    # every index of an ok batch reads clean — v3.9's "ok means filed".
    "per_index_off": {
        "kind": "source", "owner": "P3",
        "pattern": r'out\[idx\] = str\(entry\["error"\]\) if entry\.get\("error"\) else None',
        "replace": "out[idx] = None",
        "what": "per-index batch results ignored (B1 must fail)"},
    # ── adoption at the first v4 start (owner P4; beyond §15.7's list) ──
    # Adoption never runs: the first v4 start reads its file from the top,
    # which is A's prototype (U1: 30 rows and 30 cells again).
    "adoption_off": {
        "kind": "source", "owner": "P4",
        "pattern": r"if self\._adoption_due\(machine, source\):",
        "replace": "if False:",
        "what": "no adoption: the first v4 start re-reads the whole file (U1 must fail)"},
    # B's match: the line keyed on the value the factor makes of it TODAY,
    # not the raw reading — a factor changed since logging unmatches every
    # line (U3 must fail).
    "adoption_on_corrected": {
        "kind": "source", "owner": "P4",
        "pattern": r"return AdoptionLine\(offset, part, pk, lab, adoption_key\(lab, values\),",
        "replace": "return AdoptionLine(offset, part, pk, lab, adoption_key(lab, "
                   "{k: v for k, v in corrected.items() if k not in RESERVED_ROW_KEYS}),",
        "what": "adoption matches corrected values, not raw (U3 must fail)"},
    # Round 1's hole: a QC verdict that kept no raw (v3.9, machine-level
    # factor) keyed on the corrected value it holds. Once the factor has
    # changed, the standard's print looks unrecorded (U3 must fail — on the
    # legacy road, where the bench keys LabCore's rows itself).
    "adoption_qc_on_value": {
        "kind": "source", "owner": "P4",
        "pattern": r"return \{test: ADOPTION_NO_RAW\}",
        "replace": 'return {test: row.get("value")}',
        "what": "a no-raw QC verdict keyed on its corrected value (U3 must fail)"},
    # A recovered reading stays on the results road like any other: filed
    # automatically, without the person §10.2 says must decide (U2).
    "recovered_filed": {
        "kind": "source", "owner": "P4",
        "pattern": r'rows = \[r for r in rows if r\.get\(ORIGIN_KEY\) != "recovered"\]',
        "replace": "rows = list(rows)",
        "what": "recovered readings auto-filed (U2 must fail)"},
    # A bench whose journal is gone reads its file only once LEM has said
    # where its record ends (P8, `_transfer_blind`). Off, a wiped bench
    # reads its whole file from the top and sends it all again: T4/T4b's
    # records_resent leaves 0.
    "blind_mode_off": {
        "kind": "source", "owner": "P8",
        "pattern": r"if journal is None or not journal\.checkpoint_pending\(\):\n"
                   r"(\s*)return False",
        "replace": r"if True:\n\1return False",
        "what": "blind mode off: a wiped journal re-sends (T4 floods)"},
}

CODE_DIRS = ("LEM Station Module", "LEM Web Server")
SKIP_DIRS = {".venv", "venv", "__pycache__", "tests", "static", "templates", "data",
             "node_modules", ".git"}


def copy_tree(src_root, dst_root):
    for sub in CODE_DIRS:
        shutil.copytree(os.path.join(src_root, sub), os.path.join(dst_root, sub),
                        ignore=shutil.ignore_patterns(".venv", "venv", "__pycache__",
                                                      "*.pyc", "data", "*.log"))
    return dst_root


def _edits(spec):
    """A mutation's edits: `edits` (several sites, ALL of which must be
    found), or the one `pattern`/`replace` pair."""
    if spec.get("edits"):
        return list(spec["edits"])
    if spec.get("pattern"):
        return [{"pattern": spec["pattern"], "replace": spec["replace"]}]
    return []


def apply_source(name, code_root, dst_root, edits=None):
    """Copy, edit, and return (dst_root, matches). matches == 0 means
    UNAVAILABLE and nothing was edited — including when ANY one of a
    mutation's edits finds no code: half a mutation breaks less than it
    says. `edits` overrides the mutation's own (the tests use it to apply
    a subset)."""
    spec = MUTATIONS[name]
    if spec["kind"] != "source":
        raise ValueError(name + " is not a source mutation")
    edits = _edits(spec) if edits is None else list(edits)
    if not edits:
        return None, 0
    plan = []                                  # (compiled, replace, [rel])
    for e in edits:
        rx = re.compile(e["pattern"])
        hits = []
        for sub in CODE_DIRS:
            for dirpath, dirnames, files in os.walk(os.path.join(code_root, sub)):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for f in files:
                    if f.endswith(".py"):
                        p = os.path.join(dirpath, f)
                        with open(p, encoding="utf-8") as fh:
                            if rx.search(fh.read()):
                                hits.append(os.path.relpath(p, code_root))
        if not hits:
            return None, 0
        plan.append((rx, e["replace"], hits))
    copy_tree(code_root, dst_root)
    n = 0
    for rx, replace, hits in plan:
        for rel in hits:
            p = os.path.join(dst_root, rel)
            with open(p, encoding="utf-8") as fh:
                src = fh.read()
            new, k = rx.subn(replace, src)
            n += k
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(new)
    return dst_root, n


def verdict(clean, mutated):
    """`clean` / `mutated`: {scenario: bool green}. KILLED if any scenario
    green in the clean run is red under the mutation."""
    killed = sorted(s for s, ok in clean.items() if ok and not mutated.get(s, False))
    return ("KILLED" if killed else "SURVIVED"), killed
