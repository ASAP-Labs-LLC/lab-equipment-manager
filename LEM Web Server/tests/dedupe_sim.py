"""A synthetic lab whose every row is known to be genuine or a duplicate.

The dedupe classifier is judged against THIS, not against itself: each row the
simulator writes carries the truth of how it got there — a reading somebody
took, or a copy the transfer made (a restart replay, a re-sent batch, an import
that ran twice). The classifier never sees the truth column.

The cases are the ones a content rule gets wrong if it is careless:

* a genuine IDENTICAL re-test — same sample, same numbers, a later poll. The
  only thing that tells it from a replay is that it came alone, not in a
  burst. It must stay visible.
* a QC repeat — the standard run again and reading the same value.
* an archive FIRST ingest — a bench configured onto a file that already holds
  months of history arrives as one enormous poll, and none of it is a
  duplicate. It even holds two adjacent identical lines (a re-test printed
  twice by the instrument) that must survive.
* a replay of a file that holds the same reading TWICE: both replayed copies
  are duplicates, because both originals are already in the record — but a
  re-test printed while the bench was down arrives INSIDE the replay burst
  and is new. Matching by "seen before?" instead of "how many times seen?"
  gets one of those two wrong.
* a GC re-integration: the same samples re-processed with different numbers.
  Not a duplicate of anything.
* a re-sent batch (a log INSERT whose response was lost, N3): the whole
  batch twice, in one poll.
* the 08-27 labshare import's re-inserted batches, and its misread lab IDs.

Added after the round-1 critic broke the resend rule with two genuine
shapes it had not seen — both are here now, beside the copies they resemble:

* an archive first ingest in which ONE sample's two-line result is printed
  twice back to back, mid-poll, consecutive ids (genuine);
* a QC repeat with a status change between the two readings, in one poll
  (genuine) — and beside it the mirror's Multitek S shape, [qc, YELLOW ->
  GREEN] sent four times, which is a batch re-sent (a bench cannot turn
  GREEN from YELLOW four times running);
* a whole small poll that is one result printed twice (genuine);
* a stretch of samples repeated in order inside a replay poll, consecutive
  ids (the mirror's Agilent 09-11 shape: a copy);
* a storm day — a dozen replay polls in one day (O9's 08-18 shape), which
  is approved as its own unit.

Added after the round-2 critic hid a QC repeat inside a 24-row catch-up
poll ("≥ 20 rows" alone made every twin in it a copy):

* a catch-up poll of new work holding a QC standard read again, two
  standards read again together, and a Blank read again (the mirror's
  Multitek NS 08-31 shape) — all genuine;
* a restart that re-reads only the last two samples before the night's new
  rows — a copy, however short.

Added after the round-3 critic hid genuine QC repeats through the 80 %
majority rule, and a Blank and a Solvent as a "stretch of two samples":

* five QC standards read again the next morning, every value the same
  (whole degrees), in a poll of their own — genuine;
* four of them again with one new sample (80 % twins) — genuine;
* the daily Blank and Solvent run lines at the head of a 32-row overnight
  catch-up poll — genuine;
* and the shape that guard must not break (Agilent GC 2, 09-23): a whole
  file re-read that holds some readings more often than the record does,
  with a run of standards and one sample between those extra copies — the
  standards' copies are copies.

Added after the round-4 critic hid a genuine PAIR re-test (40005, 40006,
identical results) inside a 26-row catch-up poll as a "stretch of two
samples":

* pair, triple and four-sample re-tests, identical numbers, at the head, in
  the middle and at the tail of catch-up polls, one of them between
  standards that repeat too — all genuine;
* the restart that re-reads from a stale offset now re-reads six samples
  (a copy, hidden), and the TWO-line re-read moved to its own pair of
  benches beside a genuine re-test of the same two samples that writes the
  identical record. That copy's truth is `LISTED`: no rule can hide it
  without hiding the re-test, so it must be listed beside its original
  (`probable_duplicate`), visible, for a person.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

GENUINE, DUP = "genuine", "dup"
#: A real copy whose rows are, row for row, what a genuine re-test of at
#: most four samples writes. It must be LISTED (probable_duplicate beside
#: its original), never silently passed; it cannot be hidden without
#: hiding the re-test that looks exactly like it.
LISTED = "dup-listed"


class SimLab:
    def __init__(self):
        self.rows: List[dict] = []
        self.truth: Dict[int, str] = {}
        self._next = 1
        self._clock = 0

    # ── time and ids ──────────────────────────────────────────────────────
    def tick(self, seconds: int = 30) -> str:
        self._clock += seconds
        day, rem = divmod(self._clock, 86400)
        h, rem = divmod(rem, 3600)
        m, s = divmod(rem, 60)
        return "2026-08-%02dT%02d:%02d:%02d.000000" % (1 + day, h, m, s)

    def noise(self, uid: str = "other-bench") -> None:
        """Another bench's insert, so ids are not contiguous per bench."""
        self._write(uid, self.tick(1), "status_change", "", "", "", "{}",
                    GENUINE)

    def _write(self, uid, ts, kind, lab, test, value, detail, truth):
        rid = self._next
        self._next += 1
        self.rows.append({"id": rid, "machine_uid": uid, "ts": ts,
                          "kind": kind, "lab_id": lab, "test_name": test,
                          "value": value, "detail": detail})
        self.truth[rid] = truth
        return rid

    def poll(self, uid: str, lines: List[tuple], truths: List[str],
             ts: Optional[str] = None) -> List[int]:
        """One poll: every line gets the poll's `ts`, ids in order."""
        ts = ts or self.tick()
        return [self._write(uid, ts, *line, truth)
                for line, truth in zip(lines, truths)]

    # ── the lab's writers ─────────────────────────────────────────────────
    @staticmethod
    def run_line(lab: str, values: dict) -> tuple:
        return ("run", lab, "", "", json.dumps({"values": values}))

    @staticmethod
    def qc_line(std: str, test: str, value: float, *, operator=None,
                calibration_id=None, low=1.0, high=3.0) -> tuple:
        detail = {"in_spec": low <= value <= high, "expected": 2.0,
                  "low": low, "high": high}
        if operator:
            detail["operator"] = operator
        if calibration_id:
            detail["calibration_id"] = calibration_id
        return ("qc", std, test, str(value), json.dumps(detail))

    @staticmethod
    def imported_line(lab: str, test: str, value: str, src: str) -> tuple:
        return ("run", lab, test, value, json.dumps(
            {"imported": "labshare-2026-08-27", "source_file": src}))

    # ── truth ─────────────────────────────────────────────────────────────
    def ids(self, which: str) -> set:
        return {r for r, t in self.truth.items() if t == which}

    def bench_rows(self, uid: str) -> List[dict]:
        return [r for r in self.rows if r["machine_uid"] == uid]


def build() -> SimLab:
    """The whole synthetic lab. Deterministic; every case in the module
    docstring appears at least once, most of them more than once."""
    lab = SimLab()

    # ── ERA: a single_csv bench whose LabStation restarts every day ──────
    era = "era"
    era_file: List[tuple] = []
    for day in range(6):
        for k in range(8):                       # 8 genuine prints a day
            line = SimLab.run_line("4%03d%d" % (day, k),
                                   {"API": "%d.%d" % (30 + k, day),
                                    "Density": str(840 + k)})
            era_file.append(line)
            lab.poll(era, [line], [GENUINE])
            lab.noise()
        if day == 2:
            # Genuine IDENTICAL re-test, in its own later poll: the sample
            # was run again and read the same. Visible, always.
            retest = era_file[-3]
            era_file.append(retest)
            lab.poll(era, [retest], [GENUINE])
        if day == 4:
            # A re-test printed while the bench was down, identical to a
            # reading already in the record, plus one brand-new print, both
            # arriving INSIDE the restart burst. The file now holds the
            # re-tested reading twice, the record once: the second copy is
            # new.
            again = era_file[5]
            fresh = SimLab.run_line("49999", {"API": "40.0",
                                              "Density": "850"})
            era_file.extend([again, fresh])
            truths = [DUP] * (len(era_file) - 2) + [GENUINE, GENUINE]
            lab.poll(era, list(era_file), truths)
            continue
        # The daily restart re-reads the whole file from its stale offset.
        lab.poll(era, list(era_file), [DUP] * len(era_file))

    # ── SMALL: a file of 8 lines replayed whole (under the 20-row bar) ───
    small = "small"
    small_file = [SimLab.run_line("5000%d" % k, {"S": "%d.1" % k})
                  for k in range(8)]
    for line in small_file:
        lab.poll(small, [line], [GENUINE])
    lab.poll(small, list(small_file), [DUP] * 8)     # 100 % twins, 8 rows
    # 6 rows, 5 new and one genuine identical re-test: 17 % twins, so the
    # poll is not a replay and the re-test stays visible.
    new5 = [SimLab.run_line("5100%d" % k, {"S": "9.%d" % k}) for k in range(5)]
    lab.poll(small, new5 + [small_file[2]], [GENUINE] * 6)
    # A 2-row poll that is all twins: below the majority rule's floor, so it
    # is reviewed, never hidden (C's 2-row rule was rejected as too eager).
    lab.poll(small, [small_file[0], small_file[1]], [GENUINE, GENUINE])

    # ── GC: QC prints, a QC repeat, replayed QC, and a re-integration ────
    gc = "gc"
    gc_file: List[tuple] = []
    for k in range(12):
        line = SimLab.run_line("6%04d" % k, {"IBP": "1%02d.5" % k,
                                             "FBP": "3%02d.1" % k})
        gc_file.append(line)
        lab.poll(gc, [line], [GENUINE])
    for v, who in ((2.11, "ana"), (2.07, "ben"), (2.11, "ana")):
        # The third is a QC REPEAT reading the same value as the first, in
        # its own poll: genuine.
        q = SimLab.qc_line("AF26", "Sulfur", v, operator=who,
                           calibration_id="cal-1")
        gc_file.append(q)
        lab.poll(gc, [q], [GENUINE])
    # Restart replay of the whole file: QC lines come back restamped with
    # whoever is signed in now and today's calibration — still duplicates.
    replay = []
    for line in gc_file:
        if line[0] == "qc":
            d = json.loads(line[4])
            d["operator"], d["calibration_id"] = "night-shift", "cal-2"
            line = (line[0], line[1], line[2], line[3], json.dumps(d))
        replay.append(line)
    lab.poll(gc, replay, [DUP] * len(replay))
    # Re-integration: the same 12 samples re-processed, every number moved.
    reint = [SimLab.run_line("6%04d" % k, {"IBP": "1%02d.9" % k,
                                           "FBP": "3%02d.4" % k})
             for k in range(12)]
    lab.poll(gc, reint, [GENUINE] * 12)
    # ...and the next restart replays the file, re-integrated lines included.
    gc_file.extend(reint)
    lab.poll(gc, list(gc_file), [DUP] * len(gc_file))

    # ── N3: a log INSERT whose response was lost, re-sent in one poll ────
    n3 = "n3"
    batch = [SimLab.run_line("7000%d" % k, {"W": "1.%d" % k})
             for k in range(3)]
    ts = lab.tick()
    lab.poll(n3, batch, [GENUINE] * 3, ts=ts)
    lab.noise()
    lab.poll(n3, batch, [DUP] * 3, ts=ts)
    # A one-row batch re-sent, with another bench's insert in between.
    one = SimLab.run_line("70100", {"W": "5.5"})
    ts = lab.tick()
    lab.poll(n3, [one], [GENUINE], ts=ts)
    lab.noise()
    lab.poll(n3, [one], [DUP], ts=ts)

    # ── ARCH: a bench configured onto a file with history (first ingest) ─
    arch = "arch"
    history = [SimLab.run_line("8%04d" % k, {"RON": "9%d.%d" % (k % 10, k)})
               for k in range(60)]
    # The instrument printed one re-test twice, back to back: two adjacent
    # identical lines in ONE poll, both genuine.
    history.insert(30, history[29])
    lab.poll(arch, list(history), [GENUINE] * len(history))
    for k in range(3):
        line = SimLab.run_line("8100%d" % k, {"RON": "88.%d" % k})
        history.append(line)
        lab.poll(arch, [line], [GENUINE])
    lab.poll(arch, list(history), [DUP] * len(history))   # first restart

    # ── IMP: the 08-27 labshare import ───────────────────────────────────
    imp = "imp"
    lines = [SimLab.imported_line("9%03d" % k, "Flash", "6%d.0" % (k % 10),
                                  "pac_2025-11-05.csv") for k in range(25)]
    ts = "2025-11-05T10:18:47"
    lab.poll(imp, lines, [GENUINE] * 25, ts=ts)
    for _ in range(5):
        lab.noise()
    lab.poll(imp, lines, [DUP] * 25, ts=ts)          # the batch re-inserted
    # A numeric column misread as the Lab ID.
    lab.poll(imp, [SimLab.imported_line("3659736601", "Flash", "66.0",
                                        "pac_2025-11-06.csv")], [DUP],
             ts="2025-11-06T09:00:00")
    # Genuine imported readings with identical values (one row per test in
    # the import shape, and Flash reads in whole degrees): separate files,
    # separate polls, small — visible.
    lab.poll(imp, [SimLab.imported_line("9500", "Flash", "61.0",
                                        "pac_2025-11-07.csv")], [GENUINE],
             ts="2025-11-07T09:00:00")
    lab.poll(imp, [SimLab.imported_line("9500", "Flash", "61.0",
                                        "pac_2025-11-08.csv")], [GENUINE],
             ts="2025-11-08T09:00:00")
    # ── ROUND 2: the shapes the round-1 resend rule got wrong ───────────
    first = "first"
    head = [SimLab.run_line("F%03d" % k, {"RON": "9%d.%d" % (k % 10, k)})
            for k in range(40)]
    two = [SimLab.run_line("F999", {"IBP": "150.2"}),
           SimLab.run_line("F999", {"FBP": "350.9"})]
    tail = [SimLab.run_line("F%03d" % k, {"RON": "8%d.%d" % (k % 10, k)})
            for k in range(40, 70)]
    lab.poll(first, head[:17] + two + two + head[17:] + tail,
             [GENUINE] * 74)

    qc = "qcbench"
    q = SimLab.qc_line("AF26", "Sulfur", 3.042)
    green = ("status_change", "", "", "",
             json.dumps({"from": "YELLOW", "to": "GREEN"}))
    lab.poll(qc, [q, green, q], [GENUINE] * 3)          # a QC repeat
    q2 = SimLab.qc_line("AF26", "Sulfur", 2.871)
    lab.poll(qc, [q2, green] * 4,                       # sent four times
             [GENUINE, GENUINE] + [DUP, GENUINE] * 3)

    dbl = "double"
    pair = [SimLab.run_line("D1", {"IBP": "151.0"}),
            SimLab.run_line("D1", {"FBP": "351.0"})]
    lab.poll(dbl, pair + pair, [GENUINE] * 4)           # printed twice

    gc1 = "stretch"
    work = [SimLab.run_line("S1%03d" % k, {"IBP": "15%d.%d" % (k % 10, k)})
            for k in range(25)]
    for line in work:
        lab.poll(gc1, [line], [GENUINE])
    lab.poll(gc1, work + work, [DUP] * 50)    # replay, the stretch twice

    storm = "storm"
    tail_lines = [SimLab.run_line("T1%03d" % k, {"v": "t%d" % k})
                  for k in range(22)]
    for line in tail_lines:
        lab.poll(storm, [line], [GENUINE])
    for k in range(12):                       # a file rewritten in place
        lab.poll(storm, list(tail_lines), [DUP] * 22,
                 ts="2026-08-18T09:%02d:21.000000" % k)

    # ── ROUND 3: catch-up polls, where "≥ 20 rows" alone was wrong ──────
    # A bench offline overnight catches up in ONE poll of new work. Its
    # standards read what they read yesterday, and a Blank reads 0 again
    # (Multitek NS 08-31, row 216039): genuine repeats inside a big poll.
    cu = "catchup"
    af = SimLab.qc_line("AF26", "Water", 2.5)
    ao = SimLab.qc_line("AO25", "Water", 7.25)
    blank = SimLab.run_line("Blank", {"N": "1.45"})
    lab.poll(cu, [af, ao], [GENUINE, GENUINE])
    lab.poll(cu, [blank], [GENUINE])
    night = [SimLab.run_line("C1%03d" % k, {"N": "%d.%d" % (20 + k, k)})
             for k in range(30)]
    lab.poll(cu, [af] + night[:10] + [blank] + night[10:20] + [af, ao]
             + night[20:], [GENUINE] * 34)
    # ...and the other side of the line: a restart re-reads the last two
    # samples from a stale offset, then the next night's new rows follow.
    more = [SimLab.run_line("C2%03d" % k, {"N": "%d.%d" % (60 + k, k)})
            for k in range(22)]
    lab.poll(cu, night[-6:] + more, [DUP] * 6 + [GENUINE] * 22)

    # ── ROUND 4: a standard is not a sample ─────────────────────────────
    fl = "flashqc"
    stds = [SimLab.qc_line(s, "Flash", v, low=v - 3, high=v + 3)
            for s, v in (("AF26", 62.0), ("AO25", 70.0), ("AB10", 45.0),
                         ("AC11", 88.0), ("AD12", 101.0))]
    lab.poll(fl, stds, [GENUINE] * 5)
    for k in range(3):
        lab.poll(fl, [SimLab.run_line("4050%d" % k, {"F": "5%d" % k})],
                 [GENUINE])
    lab.poll(fl, stds, [GENUINE] * 5)              # the next morning
    lab.poll(fl, stds[:4] + [SimLab.run_line("40600", {"F": "60"})],
             [GENUINE] * 5)
    bs = "blanksolvent"
    blank0 = SimLab.run_line("Blank", {"N": "0.00"})
    solvent = SimLab.run_line("Solvent", {"N": "0.00"})
    lab.poll(bs, [blank0], [GENUINE])
    lab.poll(bs, [solvent], [GENUINE])
    lab.poll(bs, [blank0, solvent] + [
        SimLab.run_line("40%03d" % (700 + k), {"N": "%d.%d" % (20 + k, k)})
        for k in range(30)], [GENUINE] * 32)
    g2 = "wholefile"
    p = [SimLab.run_line("4000%d" % k, {"v": "p%d" % k}) for k in range(4)]
    q = [SimLab.run_line("AF26", {"v": "a1"}),
         SimLab.run_line("AF26", {"v": "a2"}),
         SimLab.run_line("40005", {"v": "q5"}),
         SimLab.run_line("Blank", {"v": "0"})]
    for line in p + q:
        lab.poll(g2, [line], [GENUINE])
    back = p[::-1]
    lab.poll(g2, p + back + q + back + [
        SimLab.run_line("4100%d" % k, {"v": "n%d" % k}) for k in range(10)],
        [DUP] * 4 + [GENUINE] * 4 + [DUP] * 4 + [GENUINE] * 14)

    # ── ROUND 5: a re-test of a few samples is not a re-read ────────────
    rt = "retests"
    first = [SimLab.run_line("40%03d" % k, {"S": "%d.%d" % (k % 4 + 1, k % 3),
                                            "N": "%d" % (k % 7)})
             for k in range(30)]
    lab.poll(rt, first, [GENUINE] * 30)
    for _ in range(5):
        lab.noise()

    def fresh(base, n):
        return [SimLab.run_line("%d%03d" % (base, k),
                                {"S": "%d.%d" % (k % 5, k)})
                for k in range(n)]
    # the critic's C1c: two samples re-tested back to back, mid catch-up
    lab.poll(rt, fresh(41, 10) + first[5:7] + fresh(42, 14), [GENUINE] * 26)
    # three at the head, four at the tail
    lab.poll(rt, first[10:13] + fresh(43, 22), [GENUINE] * 25)
    lab.poll(rt, fresh(44, 21) + first[20:24], [GENUINE] * 25)
    # a pair between standards that read what they read yesterday
    af, ao = SimLab.qc_line("AF26", "S", 2.0), SimLab.qc_line("AO25", "S", 2.5)
    lab.poll(rt, [af, ao], [GENUINE] * 2)
    lab.poll(rt, fresh(45, 9) + [af, first[14], first[15], ao]
             + fresh(46, 12), [GENUINE] * 25)

    # A two-line re-read and a pair re-test: the same record on two benches.
    for uid, truth in (("reread2", LISTED), ("retest2", GENUINE)):
        s = [SimLab.run_line("46%03d" % k, {"S": "%d" % (k % 3)})
             for k in range(10)]
        for line in s:
            lab.poll(uid, [line], [GENUINE])
        lab.poll(uid, s[-2:] + fresh(47, 22), [truth] * 2 + [GENUINE] * 22)
    return lab


def _is_sample_line(line: tuple) -> bool:
    import dedupe
    return line[0] == "run" and dedupe.is_sample_id(line[1])


def _sample_block(rnd, file: List[tuple], k: int) -> List[tuple]:
    """A consecutive block of `file` holding at most `k` samples (and any
    standards between them): half the time the file's last lines, half
    the time from a random place."""
    if rnd.random() < 0.5:
        e = j = len(file)
        n = 0
        while j > 0 and n + _is_sample_line(file[j - 1]) <= k:
            n += _is_sample_line(file[j - 1])
            j -= 1
        if rnd.random() < 0.5:
            while j < e and not _is_sample_line(file[j]):
                j += 1
        return file[j:e]
    j = e = rnd.randint(0, max(0, len(file) - 1))
    n = 0
    while e < len(file) and n + _is_sample_line(file[e]) <= k:
        n += _is_sample_line(file[e])
        e += 1
    return file[j:e]


def file_bench(seed: int, retest: str = "lines") -> SimLab:
    """One random single_csv bench, the round-5 critic's harness
    (`bigfuzz.py`, and the round-6 critic's `fuzz2.py`, which is the same
    generator), kept here so the suite runs what the critics ran.

    `retest="samples"` is the harsher lab the round-7 builder ran against
    its own rule: a re-test is a consecutive block of the file holding up
    to four SAMPLES, whatever standards and blanks sit between them, and
    half of those blocks are the file's last lines -- exactly what a
    short restart re-read copies. A rule that counts standards as
    evidence hides those re-tests; a rule that does not, must list the
    re-reads that look the same.

    The bench keeps a FILE: each day appends 5-40 lines — new samples
    (values at a resolution of 3, 10 or 1000 steps, so identical numbers
    are common), the AF26 check and the Blank, and re-tests of 1-4 earlier
    lines (a consecutive block or a scattered pick) that read exactly what
    they read before. Then it logs the day live (a poll per line), catches
    up (the day in one poll), or restarts and re-reads the file from a
    stale offset 5-60 lines back: those lines are copies (`DUP`), the rest
    of the poll is the day's new work (`GENUINE`)."""
    import random
    rnd = random.Random(seed)
    lab = SimLab()
    uid = "u"
    res = rnd.choice([3, 10, 1000])

    def val():
        return {"S": str(rnd.randint(1, res)) if res < 1000
                else "%.2f" % rnd.uniform(100, 400)}
    stds = [SimLab.qc_line("AF26", "S", 2.0),
            SimLab.run_line("Blank", {"S": "0"})]
    file: List[tuple] = []
    nxt = [40000]

    def newline():
        line = SimLab.run_line(str(nxt[0]), val())
        nxt[0] += 1
        return line
    logged = 0
    for _day in range(rnd.randint(2, 6)):
        today: List[tuple] = []
        for _ in range(rnd.randint(5, 40)):
            r = rnd.random()
            if r < 0.1:
                today.append(rnd.choice(stds))
            elif r < 0.2 and file:
                k = rnd.randint(1, 4)
                if retest == "samples" and rnd.random() < 0.5:
                    today.extend(_sample_block(rnd, file, k))
                elif rnd.random() < 0.5:
                    j = rnd.randint(0, max(0, len(file) - k))
                    today.extend(file[j:j + k])
                else:
                    today.extend(rnd.sample(file, min(k, len(file))))
            else:
                today.append(newline())
        file.extend(today)
        mode = rnd.choice(["live", "catchup", "restart"])
        if mode == "live":
            for line in today:
                lab.poll(uid, [line], [GENUINE])
        elif mode == "catchup":
            lab.poll(uid, today, [GENUINE] * len(today))
        else:
            stale = rnd.randint(max(0, logged - rnd.randint(5, 60)), logged)
            if logged - stale < 5:
                stale = max(0, logged - 5)
            lab.poll(uid, file[stale:], [DUP] * (logged - stale)
                     + [GENUINE] * (len(file) - logged))
        logged = len(file)
    return lab
