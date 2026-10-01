"""GateGateway: the baseline HGateway plus an analyst, and LabCore's real batch.

Built on `lemharness.HGateway` (imported read-only) so op counting and the
fault plan are byte-for-byte today's. Three additions (spec §15.4):

1. **Exact `_wop_batch`** (`batch_mode="labcore"`). LabCore runs a batch as ONE
   transaction and answers `{"ok": true, "results": [{"index": i, ...}]}` —
   `ok` even when a sub-operation failed; the failure is only in that index's
   entry. The baseline HGateway answered the first sub-error as the whole
   batch's error, which no real LabCore does. The operation names and the
   per-index error strings here are READ FROM `baseline/prod/LabCore_main.py`
   at import (the `_DB_OPS` and `_BATCH_INNER_OPS` tables), so they cannot drift
   from the source they model. `batch_mode="baseline"` keeps the old shape for
   the 29 baseline scenarios, whose numbers were measured under it.

2. **Per-index faults** (`fail_index`): one cell's sub-op answers
   `{"index": i, "error": ...}` inside an `ok` batch — before it executed, or
   (`landed=True`) after its UPDATE ran, which is what an exception in
   `_mirror_result_to_history` does in the real code (no savepoint: the UPDATE
   stays in the committed transaction).

3. **The analyst.** `analyst_edit(lab, test, value)` is a person typing a cell
   in LabEntry: it writes the cell directly and is NOT a bench op.
   `inject_between_read_and_batch(...)` arms that edit to happen after the
   bench's next read and before its next batch touching the cell — the A5
   race. The gateway then counts, at the moment each bench `update_cell`
   lands: `analyst_overwritten` (a cell the analyst set, replaced by a
   different bench value) and, for ops that carry `expect`, `expect_audit_hits`
   (the cell did not hold what the bench expected).
"""
import os
import re
from collections import Counter

from .target import BASELINE

LABCORE_MAIN = os.path.join(BASELINE, "prod", "LabCore_main.py")


def _table_keys(src, header):
    i = src.find(header)
    if i < 0:
        raise RuntimeError("LabCore_main.py: %r not found — cannot model _wop_batch" % header)
    j = src.find("\n}", i)
    return tuple(re.findall(r'^\s*"([a-z_]+)"\s*:', src[i:j], re.M))


def labcore_tables():
    """(_DB_OPS names, _BATCH_INNER_OPS names) from the real source."""
    with open(LABCORE_MAIN, encoding="utf-8") as f:     # a failed read raises
        src = f.read()
    db_ops = _table_keys(src, "_DB_OPS: dict[str, Any] = {")
    inner = _table_keys(src, "_BATCH_INNER_OPS = {")
    if "update_cell" not in db_ops or "update_cell" not in inner:
        raise RuntimeError("LabCore_main.py tables parsed without update_cell")
    # The exact strings, checked against the source so a LabCore change that
    # rewords them is noticed here rather than in a scenario.
    for s in ('f"Unknown operation: {op_name}"',
              'f"Operation {op_name} not batchable."',
              '"operations must be a non-empty list."',
              '"lab_id and test_name required."'):
        if s not in src:
            raise RuntimeError("LabCore_main.py no longer contains %s" % s)
    return db_ops, inner


INJECTED_ERROR = "CHECK constraint failed: sample_tests"


def make_gateway_class(lemharness):
    HGateway = lemharness.HGateway
    categorize = lemharness.categorize

    class GateGateway(HGateway):
        def __init__(self, batch_mode="baseline"):
            super().__init__()
            if batch_mode not in ("baseline", "labcore"):
                raise ValueError(batch_mode)
            self.batch_mode = batch_mode
            self._db_ops, self._inner_ops = labcore_tables() \
                if batch_mode == "labcore" else ((), ())
            self.index_faults = {}            # (lab, test) -> dict
            self.analyst = {}                 # (lab, test) -> value typed
            self.analyst_overwritten = Counter()   # (lab, test) -> times
            self.expect_ops = 0
            self.expect_audit_hits = Counter()
            self._inject = None
            self.injected = 0
            self.per_index_errors = Counter()      # (lab, test) -> errors answered
            self.batch_results = []                # what each batch answered

        # ── analyst ──
        def cell(self, lab, test):
            r = self.fake.read_sql("SELECT result FROM sample_tests "
                                   "WHERE lab_id=? AND test_name=?", [lab, test])
            if r.get("error"):
                raise RuntimeError("ground-truth read failed: " + r["error"])
            return r["rows"][0]["result"] if r["rows"] else None

        def analyst_edit(self, lab, test, value):
            r = self.fake.write("update_cell", {"lab_id": lab, "test_name": test,
                                                "value": value})
            if r.get("error"):
                raise RuntimeError("analyst edit failed: " + r["error"])
            self.analyst[(lab, test)] = value

        def inject_between_read_and_batch(self, lab, test, value):
            self._inject = {"cell": (lab, test), "value": value, "read_seen": False}

        def fail_index(self, lab, test, error=INJECTED_ERROR, landed=False, times=None):
            self.index_faults[(lab, test)] = {"error": error, "landed": landed,
                                              "times": times}

        # ── reads arm the injection ──
        def read_sql(self, sql, args=None, **kw):
            if self._inject is not None:
                self._inject["read_seen"] = True
            return super().read_sql(sql, args, **kw)

        # ── writes ──
        def write(self, operation, params=None, source="", **kw):
            cat = categorize("write", operation=operation)
            self.counts[cat] += 1
            self.trace.append(cat)
            ops = (params or {}).get("operations", []) if operation == "batch" else []
            for o in ops:
                p = o.get("params") or {}
                self.cell_sends[(p.get("lab_id"), p.get("test_name"), p.get("value"))] += 1

            def run():
                if operation != "batch":
                    return self.fake.write(operation, params or {})
                self._maybe_inject(ops)
                if self.batch_mode == "baseline":
                    for o in ops:
                        p = o.get("params") or {}
                        before = self._before_land(o)
                        r = self.fake.write(o["operation"], p)
                        if r.get("error"):
                            return r
                        # today's accounting: every sub-op that ran is a land
                        self.cell_lands[(p.get("lab_id"), p.get("test_name"),
                                         p.get("value"))] += 1
                        self._landed(o, before, count_land=False)
                    return {"ok": True}
                return self._wop_batch(params or {})
            res = self._apply(self._fault("write", cat, operation), run)
            if operation == "batch":
                self.batch_results.append(res)
            return res

        def _maybe_inject(self, ops):
            inj = self._inject
            if not inj or not inj["read_seen"]:
                return
            cells = {((o.get("params") or {}).get("lab_id"),
                      (o.get("params") or {}).get("test_name")) for o in ops
                     if o.get("operation") == "update_cell"}
            if inj["cell"] in cells:
                self.analyst_edit(inj["cell"][0], inj["cell"][1], inj["value"])
                self.injected += 1
                self._inject = None

        def _before_land(self, o):
            if o.get("operation") != "update_cell":
                return None
            p = o.get("params") or {}
            return self.cell(p.get("lab_id"), p.get("test_name"))

        def _landed(self, o, before, count_land=True):
            if o.get("operation") != "update_cell":
                return
            p = o.get("params") or {}
            key = (p.get("lab_id"), p.get("test_name"))
            value = p.get("value")
            if count_land:
                self.cell_lands[(key[0], key[1], value)] += 1
            if key in self.analyst and before == self.analyst[key] \
                    and str(value) != str(before):
                self.analyst_overwritten[key] += 1
            if "expect" in p:
                self.expect_ops += 1
                if (before or "") != (p.get("expect") or ""):
                    self.expect_audit_hits[key] += 1

        def _wop_batch(self, p):
            """LabCore_main._wop_batch, op for op (see module docstring)."""
            ops = p.get("operations", [])
            if not isinstance(ops, list) or not ops:
                return {"error": "operations must be a non-empty list."}
            results = []
            for i, sub in enumerate(ops):
                name = sub.get("operation", "")
                sp = sub.get("params", {}) or {}
                if name not in self._db_ops:
                    results.append({"index": i, "error": "Unknown operation: %s" % name})
                    continue
                if name not in self._inner_ops:
                    results.append({"index": i, "error": "Operation %s not batchable." % name})
                    continue
                if name == "update_cell":
                    results.append(dict(index=i, **self._inner_update_cell(sp)))
                    continue
                r = self.fake.write(name, sp)
                results.append(dict(index=i, **({"error": r["error"]} if r.get("error")
                                                 else {"ok": True})))
            return {"ok": True, "results": results}

        def _inner_update_cell(self, p):
            lab = str(p.get("lab_id", "")).strip()
            test = str(p.get("test_name", "")).strip()
            value = str(p.get("value", "")).strip()
            if not lab or not test:
                return {"error": "lab_id and test_name required."}
            fault = self.index_faults.get((lab, test))
            if fault is not None and fault["times"] is not None and fault["times"] <= 0:
                fault = None
            if fault is not None and fault["times"] is not None:
                fault["times"] -= 1
            o = {"operation": "update_cell",
                 "params": dict(p, lab_id=lab, test_name=test, value=value)}
            if fault is not None and not fault["landed"]:
                self.per_index_errors[(lab, test)] += 1
                return {"error": fault["error"]}
            before = self._before_land(o)
            r = self.fake.write("update_cell", o["params"])
            if r.get("error"):
                return {"error": r["error"]}
            self._landed(o, before)
            if fault is not None:
                self.per_index_errors[(lab, test)] += 1
                return {"error": fault["error"]}
            return {"ok": True}

        # ── LabCore op totals the economy rows use ──
        def labcore_ops(self):
            return self.ops_total()

    return GateGateway
