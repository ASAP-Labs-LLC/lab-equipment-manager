"""A reading is dated by the instrument, and a line is read once.

Ryan, 5 Oct 2026, on AF26: LEM's data export had "completely different
temperatures than GC-1 has for both corrected and uncorrected". LEM had read
the right columns. What it had got wrong was the DATE. The GC writes each
injection's time in cell 1 of its results CSV, and the module threw that away
and stamped every reading with the moment it parsed the line. Then the line
was parsed again:

* `last_position` was saved only on Settings OK, so every LabStation restart
  re-read the file from the last save;
* every re-read logged the old injection again under the new time.

So in lem_machine_log, 45 of the 56 distinct AF26 injections on the two GCs
sat under more than one date. At 2026-09-23 16:55, twenty seconds after the GC
configs were saved, every GC-1 AF26 back to 09-03 was logged again. Anybody
matching "the AF26 on the 23rd" against the GC found a different injection,
with different temperatures.

Three things, each pinned below:

1. **Result time.** A bench can mark the cell that says when the instrument
   measured (GC: cell 1). The reading is dated by it. It is never dated later
   than the moment it was read. v1 of the GC app misreads about one stamp in
   five on Python 3.11+ and can push it hours into the future, and a QC
   reading dated tomorrow would stay "fresh" for an extra day. The time it was
   received rides along on the row and in the log. A bench without the
   mapping behaves exactly as before.
2. **A line already read is not read again** in the same LabStation process:
   same Lab ID, same instrument time, same values. Only with a result time,
   because without one two identical readings are two real measurements.
3. **The position is saved as it advances,** once the poll's records are in
   LabCore. That makes a restart resume where the bench was, not where
   somebody last pressed OK.
"""
import json
import os
from datetime import datetime, timedelta

import pytest

import lem_station_module as mod
from lem_station_module import (LAB_ID_KEY, Machine, MethodMapping,
                                PrintResult, Selector, TestSpec)

from test_module_qt import FakeContext, make_module

NOW = datetime(2026, 10, 5, 16, 0, 0)

# A real GC-1 line shape: Lab ID, InjectionDateTime, 13 D2887 cuts, 13 D86
# cuts, Best Fit, Fit Score, Source File. Cells 15/17/21/25/27 are the D86
# IBP/10/50/90/FBP that GC-1's production config maps.
GC_HEADER = ("Lab ID,InjectionDateTime,2887 IBP,2887 T5,2887 T10,2887 T20,"
             "2887 T30,2887 T40,2887 T50,2887 T60,2887 T70,2887 T80,2887 T90,"
             "2887 T95,2887 FBP,D86 IBP,D86 T5,D86 T10,D86 T20,D86 T30,D86 T40,"
             "D86 T50,D86 T60,D86 T70,D86 T80,D86 T90,D86 T95,D86 FBP,"
             "Best Fit,Fit Score,Source File")


def gc_line(lab_id, when, d86_ibp=150.58, d86_50=251.08):
    d2887 = ["106.4"] * 13
    d86 = [str(d86_ibp), "180.0", "188.05", "200.0", "220.0", "238.0",
           str(d86_50), "270.0", "288.0", "307.0", "331.29", "352.0", "362.07"]
    return ",".join([lab_id, when, *d2887, *d86, "Mix", "0.450",
                     r"\\asapserver\x\AF26.CDF"])


IBP = "ASTM D2887/D86 - Distillation in Petroleum Products, IBP"
T50 = "ASTM D2887/D86 - Distillation in Petroleum Products, 50% Recovery"


def gc_machine(tmp_path, result_time=Selector(mode="cell", index=1), **over):
    base = dict(
        uid="bf8e64b59f12", title="Agilent GC 1", source_type="single_csv",
        csv_path=str(tmp_path / "distill_results.csv"), delimiter=",",
        lab_id=Selector(mode="cell", index=0),
        result_time=result_time,
        mappings=[
            MethodMapping(methods=[IBP], selector=Selector(mode="cell", index=15)),
            MethodMapping(methods=[T50], selector=Selector(mode="cell", index=21)),
        ],
        tests=[TestSpec(name=T50, value_col=T50, expected=251.37,
                        std_dev=2.08, k=1.0, sample_id="AF26")],
    )
    base.update(over)
    return Machine(**base)


# ── reading the instrument's own time ────────────────────────────────────────

class TestParseResultTime:
    @pytest.mark.parametrize("text, expected", [
        ("2026-09-11 10:59:34", datetime(2026, 9, 11, 10, 59, 34)),
        (" 2026-09-11 10:59:34 ", datetime(2026, 9, 11, 10, 59, 34)),
        ("2026-09-11T10:59:34", datetime(2026, 9, 11, 10, 59, 34)),
        ("2026-09-11 10:59", datetime(2026, 9, 11, 10, 59)),
        ("2026-09-11 10:59:34.250000", datetime(2026, 9, 11, 10, 59, 34, 250000)),
        ("09/11/2026 10:59:34", datetime(2026, 9, 11, 10, 59, 34)),
        ("09/11/2026 10:59:34 PM", datetime(2026, 9, 11, 22, 59, 34)),
        ("9/11/2026 10:59 AM", datetime(2026, 9, 11, 10, 59)),
    ])
    def test_the_stamps_benches_print(self, text, expected):
        assert mod.parse_result_time(text) == expected

    @pytest.mark.parametrize("text", [
        "", "   ", "AF26", "250.36", "2026-13-01 00:00:00", "yesterday",
        # The ChemStation compact stamp. `datetime.fromisoformat` on Python
        # 3.11+ reads this as 02:45:00, not 00:24:50: the bug that misdates
        # one GC injection in five in v1. A stamp that is not unambiguous is
        # not a time.
        "20260925002450+0000",
    ])
    def test_anything_else_is_not_a_time(self, text):
        assert mod.parse_result_time(text) is None


class TestTheMachineCarriesTheMapping:
    def test_round_trips(self, tmp_path):
        m = gc_machine(tmp_path)
        back = Machine.from_dict(json.loads(json.dumps(m.to_dict())))
        assert back.result_time.to_dict() == {"mode": "cell", "index": 1,
                                              "pattern": "", "clean": []}

    def test_a_config_saved_before_this_existed_has_none(self, tmp_path):
        data = gc_machine(tmp_path).to_dict()
        del data["result_time"]
        assert Machine.from_dict(data).result_time is None

    def test_none_round_trips_as_none(self, tmp_path):
        m = gc_machine(tmp_path, result_time=None)
        assert Machine.from_dict(m.to_dict()).result_time is None

    def test_it_is_configuration_and_travels_with_a_copy(self):
        # Not a runtime key: a duplicated GC config must keep reading cell 1.
        assert "result_time" not in mod.CONFIG_RUNTIME_KEYS


class TestParsePrint:
    def test_the_print_carries_its_instrument_time(self, tmp_path):
        result = mod.parse_print(gc_machine(tmp_path),
                                 gc_line("AF26", "2026-10-05 15:20:11"))
        assert result.lab_id == "AF26"
        assert result.result_time == datetime(2026, 10, 5, 15, 20, 11)
        assert result.values[T50] == "251.08"

    def test_no_mapping_no_time(self, tmp_path):
        result = mod.parse_print(gc_machine(tmp_path, result_time=None),
                                 gc_line("AF26", "2026-10-05 15:20:11"))
        assert result.result_time is None

    def test_an_unreadable_time_is_none_not_now(self, tmp_path):
        result = mod.parse_print(gc_machine(tmp_path),
                                 gc_line("AF26", "not a time"))
        assert result.result_time is None
        assert result.values[T50] == "251.08"   # the reading still counts


class TestToRow:
    def test_without_a_time_the_row_is_exactly_what_it_always_was(self):
        row = PrintResult(lab_id="QC1", values={"RON": "91.2"}).to_row(NOW)
        assert row == {LAB_ID_KEY: "QC1", "RON": "91.2",
                       "parsed_date": "2026-10-05", "parsed_time": "16:00:00"}

    def test_the_row_is_dated_by_the_instrument(self):
        row = PrintResult(lab_id="AF26", values={T50: "251.08"},
                          result_time=datetime(2026, 9, 23, 9, 8, 36)).to_row(NOW)
        assert (row["parsed_date"], row["parsed_time"]) == ("2026-09-23", "09:08:36")
        assert row[mod.INSTRUMENT_TIME_KEY] == "2026-09-23T09:08:36"
        assert row[mod.RECEIVED_KEY] == "2026-10-05T16:00:00"

    def test_a_time_in_the_future_is_capped_at_the_moment_it_was_read(self):
        # v1's misread pushes 00:24:50 to 02:45:00. Read at 01:00 that is a
        # reading from the future, which would keep a QC check fresh for longer
        # than it was.
        row = PrintResult(lab_id="AF26", values={T50: "251.08"},
                          result_time=NOW + timedelta(hours=2)).to_row(NOW)
        assert (row["parsed_date"], row["parsed_time"]) == ("2026-10-05", "16:00:00")
        # What the instrument said is still on the record.
        assert row[mod.INSTRUMENT_TIME_KEY] == "2026-10-05T18:00:00"

    def test_the_bookkeeping_is_never_a_measurement(self):
        for key in (mod.INSTRUMENT_TIME_KEY, mod.RECEIVED_KEY):
            assert key in mod.RESERVED_ROW_KEYS
        row = PrintResult(lab_id="AF26", values={T50: "251.08"},
                          result_time=datetime(2026, 9, 23, 9, 8, 36)).to_row(NOW)
        assert mod.run_log_detail(row)["values"] == {T50: "251.08"}


# ── through the real module ──────────────────────────────────────────────────

class Gateway:
    """The injected labcore_* helpers: records every statement, accepts all."""

    def __init__(self, refuse_log=False):
        self.sqls = []
        self.refuse_log = refuse_log

    def sql(self, sql, args=None, source="", timeout=None):
        self.sqls.append((str(sql), list(args or [])))
        if self.refuse_log and "INSERT INTO lem_machine_log" in str(sql):
            return {"error": "LabCore busy", "busy": True}
        return {"ok": True}

    def read_sql(self, sql, args=None, **kw):
        # Unanswered, so the bench keeps the QC specs it was bound with rather
        # than replacing them with an empty library.
        return {"error": "no such table"}

    def write(self, operation, params=None, source=""):
        return {"ok": True}

    def log_rows(self):
        """(ts, kind, lab_id, test_name, value, detail) of every logged record."""
        out = []
        for sql, args in self.sqls:
            if "INSERT INTO lem_machine_log" not in sql:
                continue
            for i in range(0, len(args), 7):
                uid, ts, kind, lab_id, test, value, detail = args[i:i + 7]
                out.append((ts, kind, lab_id, test, value,
                            json.loads(detail) if detail else {}))
        return out

    def position_saves(self):
        return [args for sql, args in self.sqls
                if "UPDATE lem_machine_config" in sql]


@pytest.fixture
def bench(qapp, monkeypatch):
    def build(machine, refuse_log=False):
        gw = Gateway(refuse_log=refuse_log)
        monkeypatch.setitem(mod.__dict__, "labcore_write", gw.write)
        monkeypatch.setitem(mod.__dict__, "labcore_sql", gw.sql)
        monkeypatch.setitem(mod.__dict__, "labcore_read_sql", gw.read_sql)
        monkeypatch.setattr(mod, "_in_thread", lambda fn, cb: cb(fn()))
        module = make_module(FakeContext())
        module.set_machine(machine, publish=False)
        return module, gw

    yield build


def write_lines(path, *lines, mode="w"):
    with open(path, mode, encoding="utf-8", newline="") as f:
        for line in lines:
            f.write(line + "\r\n")


class TestTheLogIsDatedByTheInstrument:
    def test_a_qc_verdict_carries_the_injection_time(self, bench, tmp_path):
        machine = gc_machine(tmp_path)
        write_lines(machine.csv_path, GC_HEADER,
                    gc_line("AF26", "2026-10-05 15:20:11"))
        module, gw = bench(machine)
        module.process_now(now=NOW)

        qc = [r for r in gw.log_rows() if r[1] == "qc"]
        assert len(qc) == 1
        ts, _kind, lab_id, test, value, detail = qc[0]
        assert (ts, lab_id, test, value) == ("2026-10-05T15:20:11", "AF26",
                                             T50, "251.08")
        assert detail["received_at"] == "2026-10-05T16:00:00"
        assert detail["instrument_time"] == "2026-10-05T15:20:11"
        module.shutdown()

    def test_a_run_carries_it_too(self, bench, tmp_path):
        machine = gc_machine(tmp_path)
        write_lines(machine.csv_path, gc_line("40329", "2026-10-05 14:02:00"))
        module, gw = bench(machine)
        module.process_now(now=NOW)

        runs = [r for r in gw.log_rows() if r[1] == "run" and r[2] == "40329"]
        assert [r[0] for r in runs] == ["2026-10-05T14:02:00"]
        assert runs[0][5]["received_at"] == "2026-10-05T16:00:00"
        module.shutdown()

    def test_a_bench_without_the_mapping_logs_the_read_time_as_before(
            self, bench, tmp_path):
        machine = gc_machine(tmp_path, result_time=None)
        write_lines(machine.csv_path, gc_line("AF26", "2026-10-05 15:20:11"))
        module, gw = bench(machine)
        module.process_now(now=NOW)

        qc = [r for r in gw.log_rows() if r[1] == "qc"]
        assert [r[0] for r in qc] == ["2026-10-05T16:00:00"]
        assert "received_at" not in qc[0][5]
        module.shutdown()

    def test_the_journal_record_is_dated_by_the_injection_too(
            self, bench, tmp_path):
        # Added at the v4 merge. On v4 the log rows above are not built at
        # projection time: they are written into the journal's `run` record
        # (`_journal_poll`) and every road sends them as written — the legacy
        # projection, a restart's re-projection, and a v2 bench's sync to LEM,
        # whose server takes each row's own `ts`. So the record is where the
        # injection time has to be, or a v2 bench would date AF26 by the poll.
        machine = gc_machine(tmp_path)
        write_lines(machine.csv_path, gc_line("AF26", "2026-10-05 15:20:11"))
        module, _gw = bench(machine)
        module.process_now(now=NOW)

        runs = [r for r in module._journal._scan()
                if r["kind"] == "run" and r.get("lab_id") == "AF26"]
        assert len(runs) == 1
        (log,) = runs[0]["log"]
        assert (log[1], log[2]) == ("2026-10-05T15:20:11", "qc")
        detail = json.loads(log[6])
        assert detail["instrument_time"] == "2026-10-05T15:20:11"
        assert detail["received_at"] == "2026-10-05T16:00:00"
        module.shutdown()

    def test_an_old_standard_read_today_is_not_fresh_qc(self, tmp_path):
        # The replay that made 09-03's AF26 look like 09-23's QC. Read today, an
        # injection from twelve days ago is twelve days old, and a 24-hour
        # check on it is stale.
        machine = gc_machine(tmp_path)
        row = mod.parse_print(
            machine, gc_line("AF26", "2026-09-23 09:08:36")).to_row(NOW)
        evaluation = mod.evaluate_machine(machine, [row], NOW)

        result = next(r for r in evaluation.test_results if r.name == T50)
        assert result.time == datetime(2026, 9, 23, 9, 8, 36)
        assert result.in_spec is True                # the reading itself is fine
        assert evaluation.status != mod.STATUS_GREEN  # but it is not today's

        fresh = mod.parse_print(
            machine, gc_line("AF26", "2026-10-05 15:20:11")).to_row(NOW)
        assert mod.evaluate_machine(machine, [fresh], NOW).status == \
            mod.STATUS_GREEN


class TestALineIsReadOnce:
    def test_a_re_read_logs_nothing_new(self, bench, tmp_path):
        machine = gc_machine(tmp_path)
        write_lines(machine.csv_path, GC_HEADER,
                    gc_line("AF26", "2026-10-05 15:20:11"),
                    gc_line("40329", "2026-10-05 15:40:00"))
        module, gw = bench(machine)
        module.process_now(now=NOW)

        def readings():
            # The header line is logged as a 'run' with Lab ID "Lab ID" on
            # every read, as it always was. It carries no time, so it is not
            # this change's to drop.
            return [r for r in gw.log_rows() if r[2] in ("AF26", "40329")]

        before = len(readings())
        assert before == 2

        # The same injections arrive again. Under v3.9 that was the file read
        # from the top (a position pulled back by a config save). Under v4 the
        # cursor no longer takes its position from `last_position` and the
        # journal's store check drops a re-read of the same bytes at the same
        # offset; what still reaches the parser is the same injection at a NEW
        # offset — the GC hub appending it again, or a rewrite the resolver
        # can only call ambiguous. That is what `drop_repeats` is for now.
        write_lines(machine.csv_path,
                    gc_line("AF26", "2026-10-05 15:20:11"),
                    gc_line("40329", "2026-10-05 15:40:00"), mode="a")
        module.process_now(now=NOW + timedelta(minutes=1))

        assert len(readings()) == before
        module.shutdown()

    def test_a_genuinely_new_injection_still_lands(self, bench, tmp_path):
        machine = gc_machine(tmp_path)
        write_lines(machine.csv_path, gc_line("AF26", "2026-10-05 15:20:11"))
        module, gw = bench(machine)
        module.process_now(now=NOW)

        # The old injection again (see `test_a_re_read_logs_nothing_new` for
        # why v4 sees it as an append) and a new one behind it.
        write_lines(machine.csv_path, gc_line("AF26", "2026-10-05 15:20:11"),
                    gc_line("AF26", "2026-10-05 15:50:00"), mode="a")
        module.process_now(now=NOW + timedelta(minutes=1))

        qc_times = [r[0] for r in gw.log_rows() if r[1] == "qc"]
        assert qc_times == ["2026-10-05T15:20:11", "2026-10-05T15:50:00"]
        module.shutdown()

    def test_the_same_time_with_different_values_is_a_new_result(
            self, bench, tmp_path):
        # A reprocessed sample: the hub can append the same injection again
        # with new numbers. That is a new result, not a repeat.
        machine = gc_machine(tmp_path)
        write_lines(machine.csv_path, gc_line("40329", "2026-10-05 15:20:11"))
        module, gw = bench(machine)
        module.process_now(now=NOW)
        write_lines(machine.csv_path,
                    gc_line("40329", "2026-10-05 15:20:11", d86_50=249.9),
                    mode="a")
        module.process_now(now=NOW + timedelta(minutes=1))

        runs = [r for r in gw.log_rows() if r[1] == "run"]
        assert [r[5]["values"][T50] for r in runs] == ["251.08", "249.9"]
        module.shutdown()

    def test_without_a_result_time_identical_lines_are_two_readings(
            self, bench, tmp_path):
        # Two identical sulfur readings a minute apart are two measurements.
        # With nothing to date them by, nothing can tell a repeat from a rerun,
        # so nothing is dropped.
        machine = gc_machine(tmp_path, result_time=None)
        write_lines(machine.csv_path, gc_line("40329", "2026-10-05 15:20:11"))
        module, gw = bench(machine)
        module.process_now(now=NOW)
        # The identical line printed again (v4 reads by cursor, not by
        # `last_position`, so "again" is an append).
        write_lines(machine.csv_path, gc_line("40329", "2026-10-05 15:20:11"),
                    mode="a")
        module.process_now(now=NOW + timedelta(minutes=1))

        assert len([r for r in gw.log_rows() if r[1] == "run"]) == 2
        module.shutdown()


class TestThePositionIsSavedAsItAdvances:
    def test_saved_once_the_records_are_in(self, bench, tmp_path):
        machine = gc_machine(tmp_path)
        write_lines(machine.csv_path, gc_line("AF26", "2026-10-05 15:20:11"))
        module, gw = bench(machine)
        module.process_now(now=NOW)

        saves = gw.position_saves()
        assert saves == [[module.machine().last_position, "bf8e64b59f12"]]
        sql = next(s for s, _a in gw.sqls if "UPDATE lem_machine_config" in s)
        # Only the position: a whole-config write here would undo anything the
        # floor changed since this bench last loaded it.
        assert "json_set(config, '$.last_position', ?)" in sql
        module.shutdown()

    def test_a_poll_with_nothing_new_writes_nothing(self, bench, tmp_path):
        machine = gc_machine(tmp_path)
        write_lines(machine.csv_path, gc_line("AF26", "2026-10-05 15:20:11"))
        module, gw = bench(machine)
        module.process_now(now=NOW)
        module.process_now(now=NOW + timedelta(seconds=30))
        assert len(gw.position_saves()) == 1
        module.shutdown()

    def test_the_position_a_bench_was_bound_at_is_not_written_back(
            self, bench, tmp_path):
        machine = gc_machine(tmp_path)
        write_lines(machine.csv_path, gc_line("AF26", "2026-10-05 15:20:11"))
        machine.last_position = os.path.getsize(machine.csv_path)
        module, gw = bench(machine)
        module.process_now(now=NOW)
        assert gw.position_saves() == []
        module.shutdown()

    def test_not_saved_while_the_log_write_is_refused(self, bench, tmp_path):
        # Saved ahead of a refused log write, a restart would skip lines whose
        # only record was still sitting in this process's memory.
        machine = gc_machine(tmp_path)
        write_lines(machine.csv_path, gc_line("AF26", "2026-10-05 15:20:11"))
        module, gw = bench(machine, refuse_log=True)
        module.process_now(now=NOW)
        assert gw.position_saves() == []
        module.shutdown()

    def test_a_v2_bench_writes_no_position_to_labcore(self, bench, tmp_path,
                                                      monkeypatch):
        # Added at the v4 merge. A v2 bench's LabCore road is the results road
        # and nothing else (D2), and its position lives in the journal's
        # cursor.json; the mirror into lem_machine_config is for a bench on
        # the legacy road, which a rollback to v3.9 would resume from.
        machine = gc_machine(tmp_path)
        write_lines(machine.csv_path, gc_line("AF26", "2026-10-05 15:20:11"))
        module, gw = bench(machine)
        module.machine().last_position = 4096
        monkeypatch.setattr(mod, "_v2", lambda _self: True)
        module._save_position(module.machine(), gw.sql)
        assert gw.position_saves() == []
        module.shutdown()

    def test_only_a_tailed_file_has_a_position(self, bench, tmp_path):
        machine = gc_machine(tmp_path, source_type="manual")
        module, gw = bench(machine)
        module.process_now(now=NOW)
        assert gw.position_saves() == []
        module.shutdown()


# ── what still runs on the clock it was read by ─────────────────────────────

class TestWaitingIsCountedFromReceipt:
    """The instrument's time dates the MEASUREMENT. How long the bench has been
    waiting on something is a different question, and its clock starts when the
    line arrived. A Friday injection a bench only gets on Monday has waited
    since Monday."""

    def row(self, measured, received):
        return PrintResult(lab_id="40329", values={T50: "251.08"},
                           result_time=measured).to_row(received)

    def test_a_held_reading_is_not_given_up_on_for_its_injection_age(self):
        row = self.row(NOW - timedelta(days=9), NOW - timedelta(hours=1))
        keep, expired = mod.expire_held_rows([row], NOW)
        assert (keep, expired) == ([row], [])

    def test_a_reading_held_for_the_whole_week_still_is(self):
        row = self.row(NOW - timedelta(days=9), NOW - timedelta(days=8))
        keep, expired = mod.expire_held_rows([row], NOW)
        assert (keep, expired) == ([], [row])

    def test_a_just_received_reading_is_asked_about_on_the_fast_clock(self):
        row = self.row(NOW - timedelta(days=2), NOW - timedelta(minutes=5))
        ids, swept = mod.identity_lookup_ids([row], NOW, last_sweep=NOW)
        assert (ids, swept) == (["40329"], False)

    def test_the_floor_hears_when_the_line_was_read(self):
        row = self.row(NOW - timedelta(hours=3), NOW)
        payload = mod.build_live_payload(
            gc_machine_plain(), mod.MachineEvaluation(status="GREEN", reason=""),
            NOW, 30, [row])
        assert payload["last_parse_at"] == NOW.isoformat()


def gc_machine_plain():
    return Machine(uid="bf8e64b59f12", title="Agilent GC 1")


# ── the setup dialog ─────────────────────────────────────────────────────────

GC_TEMPLATE = gc_line("37376", "2026-08-06 15:50:00")


def setup_dialog(**machine_kw):
    base = dict(uid="bf8e64b59f12", title="Agilent GC 1", template=GC_TEMPLATE,
                lab_id=Selector(mode="cell", index=0))
    base.update(machine_kw)
    return mod._MachineDialog(Machine(**base), None)


class TestMarkingTheResultTimeCell:
    def test_the_selected_cell_becomes_the_result_time(self, qapp):
        d = setup_dialog()
        d._cells.setCurrentCell(0, 1)
        d._set_result_time()
        assert d._result_time.to_dict() == {"mode": "cell", "index": 1,
                                            "pattern": "", "clean": []}
        saved = Machine(uid="bf8e64b59f12")
        d._write_fields_into(saved)
        assert saved.result_time.index == 1

    def test_the_preview_says_what_it_read(self, qapp):
        d = setup_dialog()
        d._cells.setCurrentCell(0, 1)
        d._set_result_time()
        cells = {d._preview.item(r, 0).text(): d._preview.item(r, 1).text()
                 for r in range(d._preview.rowCount())}
        assert cells["Result time"] == "2026-08-06 15:50:00"

    def test_a_cell_that_is_not_a_time_says_so_in_the_preview(self, qapp):
        d = setup_dialog()
        d._cells.setCurrentCell(0, 2)            # a temperature, not a time
        d._set_result_time()
        cells = {d._preview.item(r, 0).text(): d._preview.item(r, 1).text()
                 for r in range(d._preview.rowCount())}
        assert cells["Result time"].startswith("(not a time")

    def test_cleared_it_is_none_again(self, qapp):
        d = setup_dialog(result_time=Selector(mode="cell", index=1))
        d._clear_result_time()
        saved = Machine(uid="bf8e64b59f12")
        d._write_fields_into(saved)
        assert saved.result_time is None

    def test_a_saved_mapping_is_shown_when_the_dialog_opens(self, qapp):
        d = setup_dialog(result_time=Selector(mode="cell", index=1))
        assert d._result_time.index == 1
        rows = [d._preview.item(r, 0).text() for r in range(d._preview.rowCount())]
        assert "Result time" in rows

    def test_no_mapping_no_preview_row(self, qapp):
        d = setup_dialog()
        rows = [d._preview.item(r, 0).text() for r in range(d._preview.rowCount())]
        assert "Result time" not in rows


class TestAnUndatedBenchLogsExactlyAsBefore:
    def test_the_log_keeps_the_read_moment_to_the_microsecond(self, bench,
                                                              tmp_path):
        # The row's own date is kept to the second; the log's `ts` never was.
        # A bench without the mapping must not lose that precision (it orders
        # same-second records) just because its rows are now read for a date.
        machine = gc_machine(tmp_path, result_time=None)
        write_lines(machine.csv_path, gc_line("40329", "2026-10-05 15:20:11"))
        module, gw = bench(machine)
        precise = NOW.replace(microsecond=481926)
        module.process_now(now=precise)
        runs = [r for r in gw.log_rows() if r[2] == "40329"]
        assert [r[0] for r in runs] == [precise.isoformat()]
        module.shutdown()
