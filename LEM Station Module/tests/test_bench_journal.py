"""The bench journal: the bench's own custody of every reading (transfer v4 §3).

Until v4 a reading lived in this module's memory between being taken off the
instrument and landing in LabCore, and a LabStation restart, a crash or a
shift-change reboot in that window took it with it. Phase 1 measured it: kill
the serial bench after the frames are read and before the log write, and three
readings are in no store at all (K8); kill it while readings wait for their
sample, and three results never reach LabCore (K9).

The journal closes that window. Every reading is appended to a JSON-lines file
in LabStation's data directory and fsync'd BEFORE anything downstream acts on
it, so whatever happens next, the bench still holds it. Being the custody of
last resort, the file has to keep a handful of promises that these tests pin:

  * Records are numbered contiguously per epoch. The server will hold "the
    highest contiguous seq" as its cursor (§6.1); a gap would be a reading the
    cursor silently steps over.
  * One fsync per append call, not per record and not per idle poll. fsync is
    the expensive part on a bench PC, and an idle poll must cost nothing.
  * Every line carries a CRC of its exact bytes, so a flipped bit is a
    rejected line, never a different reading.
  * A torn tail — the half-written last line a power cut leaves — is cut off
    on open, set aside for a human, and appending resumes at the next seq.
  * The epoch is minted only when journal.meta is MISSING. An unreadable meta
    is not a missing one (a failed read is never an empty result): it is
    rebuilt from the records, or the journal refuses to open.
"""
import hashlib
import json
import os
import zlib
from datetime import datetime, timedelta

import pytest

import lem_station_module as mod


NOW = datetime(2026, 10, 1, 9, 0, 0)


def journal(tmp_path, **kw):
    return mod.BenchJournal(str(tmp_path / "j" / "b1"), "b1", **kw)


def lines_of(path):
    with open(path, "rb") as f:
        return f.read().splitlines(True)


def segment_paths(j):
    return [os.path.join(j.dir, n) for n in sorted(os.listdir(j.dir))
            if n.startswith("seg-") and n.endswith(".jsonl")]


def records(j):
    out = []
    for p in segment_paths(j):
        for line in lines_of(p):
            body = mod.parse_journal_line(line)
            assert body is not None, line
            out.append(body)
    return out


class CountingFsync:
    def __init__(self):
        self.calls = 0

    def __call__(self, fd):
        self.calls += 1
        os.fsync(fd)


# ── numbering ─────────────────────────────────────────────────────────────────

def test_seq_is_contiguous_across_appends_and_reopen(tmp_path):
    """The server's cursor is "the highest CONTIGUOUS seq it holds". A journal
    that skipped a number — on reopen, on a multi-record append — would make a
    reading the cursor can step over without ever having received it."""
    j = journal(tmp_path)
    j.append([{"kind": "run", "lab_id": "A"}])
    j.append([{"kind": "run", "lab_id": "B"}, {"kind": "run", "lab_id": "C"}])
    j.close()
    j = journal(tmp_path)
    j.append([{"kind": "run", "lab_id": "D"}])
    seqs = [r["seq"] for r in records(j)]
    assert seqs == [1, 2, 3, 4]
    assert len({r["epoch"] for r in records(j)}) == 1


def test_append_returns_refs_naming_epoch_and_seq(tmp_path):
    j = journal(tmp_path)
    refs = j.append([{"kind": "run"}, {"kind": "run"}])
    assert refs == ["%s:1" % j.epoch, "%s:2" % j.epoch]


def test_segments_roll_and_numbering_continues(tmp_path):
    """Segments roll at a size bound so retention can delete whole files; the
    numbering must not notice the roll."""
    j = journal(tmp_path, segment_bytes=300)
    for i in range(12):
        j.append([{"kind": "run", "lab_id": "L%02d" % i, "pad": "x" * 60}])
    assert len(segment_paths(j)) > 2
    assert [r["seq"] for r in records(j)] == list(range(1, 13))


# ── fsync ─────────────────────────────────────────────────────────────────────

def test_exactly_one_fsync_per_append_whatever_its_size(tmp_path):
    """fsync is the cost of custody, paid once per append call: a poll that
    produced fifty readings appends them in one call and pays once."""
    j = journal(tmp_path)
    j.append([{"kind": "run"}])          # first append creates the segment
    counter = CountingFsync()
    j._fsync = counter
    j.append([{"kind": "run"}])
    assert counter.calls == 1
    j.append([{"kind": "run", "n": i} for i in range(50)])
    assert counter.calls == 2


def test_an_empty_append_writes_and_fsyncs_nothing(tmp_path):
    """An idle poll has nothing to append and must cost the disk nothing."""
    j = journal(tmp_path)
    counter = CountingFsync()
    j._fsync = counter
    assert j.append([]) == []
    assert counter.calls == 0
    assert segment_paths(j) == []


def test_a_failed_write_leaves_no_partial_record_and_no_hole(tmp_path):
    """A write that fails half way (disk full) must not leave half a record for
    the next open to call a torn tail, nor a seq the next append skips."""
    j = journal(tmp_path)
    j.append([{"kind": "run"}])

    def boom(fd):
        raise OSError(28, "No space left on device")
    j._fsync = boom
    with pytest.raises(mod.JournalError):
        j.append([{"kind": "run", "lab_id": "lost?"}])
    j._fsync = os.fsync
    j.append([{"kind": "run", "lab_id": "next"}])
    rs = records(j)
    assert [r["seq"] for r in rs] == [1, 2]
    assert rs[1]["lab_id"] == "next"


# ── CRC ───────────────────────────────────────────────────────────────────────

def test_the_crc_rejects_every_single_flipped_byte(tmp_path):
    """The CRC is over the exact bytes of the canonical body, so ANY changed
    byte — in a value, a key, the crc itself or the framing — makes the line
    unreadable rather than a different reading. Checked at every position."""
    j = journal(tmp_path)
    j.append([{"kind": "run", "lab_id": "100126-10003",
               "values": {"Density": "0.8003"}}])
    line = lines_of(segment_paths(j)[0])[0]
    assert mod.parse_journal_line(line) is not None
    for i in range(len(line) - 1):                 # every byte but the newline
        flipped = bytearray(line)
        flipped[i] ^= 0x01
        assert mod.parse_journal_line(bytes(flipped)) is None, \
            "byte %d flipped and the line still parsed" % i


def test_a_line_without_its_newline_is_not_a_record(tmp_path):
    """A line the writer never finished is torn even if its prefix happens to
    be valid JSON: only a complete line was ever fsync'd as a whole."""
    j = journal(tmp_path)
    j.append([{"kind": "run"}])
    line = lines_of(segment_paths(j)[0])[0]
    assert mod.parse_journal_line(line[:-1]) is None


def test_line_format_is_canonical_json_plus_crc_of_the_body(tmp_path):
    """The record is plain JSON-lines (sorted keys) with the CRC as the last
    field, so a person can read it and another program can verify it without
    this module: crc32 over the line minus its crc field."""
    j = journal(tmp_path)
    j.append([{"kind": "run", "lab_id": "X", "b": 1, "a": 2}])
    line = lines_of(segment_paths(j)[0])[0]
    obj = json.loads(line)
    crc = obj.pop("crc")
    body = json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    assert crc == "%08x" % (zlib.crc32(body) & 0xffffffff)
    assert list(obj) == sorted(obj)


# ── torn tail ────────────────────────────────────────────────────────────────

def test_a_torn_tail_is_cut_off_set_aside_and_the_seq_resumes(tmp_path):
    """The T5 shape: power fails mid-append, the last line is half on disk.
    On open the reader cuts at the first line that fails, keeps the bytes it
    cut in a file a person can look at, and the next append takes the next
    number — the torn record's reading was never acted on (nothing downstream
    runs before the fsync), so the source still owes it and it comes back."""
    j = journal(tmp_path)
    j.append([{"kind": "run", "lab_id": "A"}])
    j.append([{"kind": "run", "lab_id": "B"}, {"kind": "run", "lab_id": "C"}])
    j.close()
    seg = segment_paths(j)[-1]
    data = open(seg, "rb").read()
    last = data.splitlines(True)[-1]
    left = 7
    with open(seg, "r+b") as f:
        f.truncate(len(data) - len(last) + left)   # only `left` bytes survived
    j = journal(tmp_path)
    assert [r["lab_id"] for r in records(j)] == ["A", "B"]
    # what the power cut LEFT of the line is what is cut off and set aside
    assert j.repairs and j.repairs[0]["cut_bytes"] == left
    torn = [n for n in os.listdir(j.dir) if n.startswith("torn-")]
    assert len(torn) == 1
    assert open(os.path.join(j.dir, torn[0]), "rb").read() == last[:left]
    j.append([{"kind": "run", "lab_id": "C again"}])
    assert [r["seq"] for r in records(j)] == [1, 2, 3]


def test_garbage_after_the_torn_line_is_cut_too(tmp_path):
    """"Truncate at the FIRST line that fails": a power loss can leave a later
    page of the unfsynced append on disk while an earlier one is zeros. A
    valid-looking line after a bad one was never part of a completed append."""
    j = journal(tmp_path)
    j.append([{"kind": "run", "lab_id": "A"}])
    j.append([{"kind": "run", "lab_id": "B"}, {"kind": "run", "lab_id": "C"}])
    j.close()
    seg = segment_paths(j)[-1]
    ls = lines_of(seg)
    ls[1] = b"\x00" * len(ls[1])
    with open(seg, "wb") as f:
        f.writelines(ls)
    j = journal(tmp_path)
    assert [r["lab_id"] for r in records(j)] == ["A"]


def test_a_bad_line_in_an_older_segment_is_skipped_and_reported_not_cut(tmp_path):
    """Only the LAST segment can have a torn tail; a bad line anywhere else is
    damage, not a power cut. Cutting there would throw away every later record,
    so it is skipped, counted, and said."""
    j = journal(tmp_path, segment_bytes=200)
    for i in range(6):
        j.append([{"kind": "run", "lab_id": "L%d" % i, "pad": "y" * 60}])
    j.close()
    first = segment_paths(j)[0]
    ls = lines_of(first)
    ls[0] = ls[0].replace(b"L0", b"LX")
    with open(first, "wb") as f:
        f.writelines(ls)
    j = journal(tmp_path, segment_bytes=200)
    assert j.corrupt_lines == 1
    assert [r for r in j.notices if "damaged" in r]
    assert len([r for r in j._scan() if r.get("kind") == "run"]) == 5


# ── epoch and journal.meta ───────────────────────────────────────────────────

def test_the_epoch_survives_reopen(tmp_path):
    j = journal(tmp_path)
    epoch = j.epoch
    j.append([{"kind": "run"}])
    j.close()
    assert journal(tmp_path).epoch == epoch


def test_a_new_epoch_is_minted_only_when_journal_meta_is_missing(tmp_path):
    """The server keeps one cursor per (uid, epoch). A new epoch tells it "this
    bench starts numbering again"; minting one for any reason other than the
    meta file being gone would fork the record's numbering."""
    j = journal(tmp_path)
    old = j.epoch
    j.append([{"kind": "run"}])
    j.close()
    os.remove(os.path.join(j.dir, "journal.meta"))
    j2 = journal(tmp_path)
    assert j2.epoch != old
    assert j2.append([{"kind": "run"}]) == ["%s:1" % j2.epoch]


def test_an_unreadable_meta_is_rebuilt_from_the_records_not_reminted(tmp_path):
    """A failed read is never an empty result. journal.meta that cannot be
    parsed is not a missing one: the records carry their epoch, so the meta is
    rebuilt from them and numbering continues."""
    j = journal(tmp_path)
    old = j.epoch
    j.append([{"kind": "run"}, {"kind": "run"}])
    j.close()
    with open(os.path.join(j.dir, "journal.meta"), "w") as f:
        f.write("{not json")
    j2 = journal(tmp_path)
    assert j2.epoch == old
    assert j2.append([{"kind": "run"}]) == ["%s:3" % old]
    assert [n for n in j2.notices if "journal.meta" in n]


def test_an_unreadable_meta_with_no_records_refuses_to_open(tmp_path):
    """With nothing to rebuild from, the journal cannot know its epoch and
    will not guess one."""
    d = tmp_path / "j" / "b1"
    d.mkdir(parents=True)
    (d / "journal.meta").write_text("garbage")
    with pytest.raises(mod.JournalError):
        journal(tmp_path)


def test_meta_holds_the_spec_fields(tmp_path):
    j = journal(tmp_path)
    meta = json.loads(open(os.path.join(j.dir, "journal.meta")).read())
    for key in ("epoch", "acked", "durable", "created", "module",
                "last_v2_handshake"):
        assert key in meta
    assert meta["acked"] == 0 and meta["durable"] == 0


def test_set_acked_is_persisted_atomically(tmp_path):
    j = journal(tmp_path)
    j.append([{"kind": "run"}] * 3)
    j.set_acked(3, durable=2)
    j.close()
    j = journal(tmp_path)
    assert (j.acked, j.durable) == (3, 2)
    assert not [n for n in os.listdir(j.dir) if n.endswith(".tmp")]


# ── atomic replace ───────────────────────────────────────────────────────────

def test_atomic_replace_retries_a_windows_sharing_violation(tmp_path, monkeypatch):
    """On Windows an antivirus or a backup agent holding journal.meta open
    makes os.replace fail with a sharing violation for a moment. The write is
    retried 3 times, 50 ms apart, before it is an error."""
    target = str(tmp_path / "f.json")
    real = os.replace
    fails = {"n": 2}
    sleeps = []

    def flaky(src, dst):
        if fails["n"]:
            fails["n"] -= 1
            raise PermissionError(13, "sharing violation")
        return real(src, dst)
    monkeypatch.setattr(mod.os, "replace", flaky)
    monkeypatch.setattr(mod, "_sleep", sleeps.append)
    mod.atomic_write(target, b"{}")
    assert open(target, "rb").read() == b"{}"
    assert sleeps == [0.05, 0.05]


def test_atomic_replace_gives_up_after_three_retries(tmp_path, monkeypatch):
    calls = {"n": 0}

    def always(src, dst):
        calls["n"] += 1
        raise PermissionError(13, "sharing violation")
    monkeypatch.setattr(mod.os, "replace", always)
    monkeypatch.setattr(mod, "_sleep", lambda s: None)
    with pytest.raises(PermissionError):
        mod.atomic_write(str(tmp_path / "f.json"), b"{}")
    assert calls["n"] == 4                       # the try plus 3 retries
    assert not [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]


# ── bench.key ────────────────────────────────────────────────────────────────

def test_bench_key_is_written_once_and_readable_only_by_its_owner(tmp_path):
    """The per-bench token (§6.4) lives here and not in the canvas file, so a
    canvas export cannot leak it. Written once at enrolment; a second write is
    a re-enrolment, which is an admin decision and not this file's."""
    j = journal(tmp_path)
    assert j.read_bench_key() is None
    j.write_bench_key("tok-123")
    assert j.read_bench_key() == "tok-123"
    if os.name != "nt":
        assert oct(os.stat(os.path.join(j.dir, "bench.key")).st_mode & 0o777) \
            == oct(0o600)
    with pytest.raises(mod.JournalError):
        j.write_bench_key("tok-456")
    assert j.read_bench_key() == "tok-123"


# ── digest ───────────────────────────────────────────────────────────────────

def test_running_digest_is_a_sha256_chain_over_the_canonical_bodies(tmp_path):
    """Every sync will carry the digest of records 1..acked so the server can
    say "this bench and LEM disagree from seq N" (§3.1). Both ends have to be
    able to carry it across a restart — the bench after its old segments are
    pruned, the server between two requests — and a hashlib object cannot be
    saved. So "running" is a chain, resumable from its last hex value:

        d0 = 64 zeros;  d_n = sha256(bytes.fromhex(d_{n-1}) + body_n)

    over the canonical body bytes (sorted keys, no crc), in seq order. The
    server computes exactly this from what it stored; this test computes it
    independently of the journal's own code."""
    j = journal(tmp_path)
    j.append([{"kind": "run", "lab_id": "A"}, {"kind": "run", "lab_id": "B"}])
    j.append([{"kind": "run", "lab_id": "C"}])
    d = "0" * 64
    for r in records(j)[:2]:
        r.pop("crc", None)
        body = json.dumps(r, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8")
        d = hashlib.sha256(bytes.fromhex(d) + body).hexdigest()
    assert j.digest(2) == d
    assert mod.running_digest("0" * 64, b"x") == \
        hashlib.sha256(bytes(32) + b"x").hexdigest()
    before = j.digest(2)
    j.append([{"kind": "run", "lab_id": "D"}])
    assert j.digest(2) == before                 # later records do not move it
    assert j.digest(4) != before


# ── recovery state ───────────────────────────────────────────────────────────

def test_open_runs_are_those_not_both_projected_and_settled(tmp_path):
    """What a restart must re-deliver: a run whose log row never reached the
    record (unprojected) or whose result was never filed (unsettled)."""
    j = journal(tmp_path)
    a, b, c = j.append([{"kind": "run", "lab_id": x} for x in "ABC"])
    j.mark_projected([a, b])
    j.mark_settled([a, c])
    j.close()
    j = journal(tmp_path)
    state = {r["ref"]: (r["projected"], r["settled"]) for r in j.open_runs()}
    assert state == {b: (True, False), c: (False, True)}


def test_consumed_keys_and_unconsumed_frames_survive_reopen(tmp_path):
    j = journal(tmp_path)
    pk1 = j.append_frame("100126-1,0.8")
    pk2 = j.append_frame("100126-2,0.8")
    j.append([{"kind": "consumed", "pks": [pk1]}])
    j.close()
    j = journal(tmp_path)
    assert j.known(pk1) and not j.known(pk2)
    assert [(pk, text) for pk, text in j.pending_frames()] == [(pk2, "100126-2,0.8")]
    pk3 = j.append_frame("x")
    assert pk3 != pk2 and pk3 != pk1             # frame numbers never reused


# ── retention ────────────────────────────────────────────────────────────────

def _age(j, days):
    t = (datetime.now() - timedelta(days=days)).timestamp()
    for p in segment_paths(j):
        os.utime(p, (t, t))


def test_retention_keeps_a_segment_until_durable_and_30_days_old(tmp_path):
    """The bench is the second copy of the record for 30 days after each
    server backup (§3.4): a segment goes only when every record in it is in a
    completed backup (<= durable) AND it is older than 30 days AND nothing in
    it is still owed to LabCore."""
    j = journal(tmp_path, segment_bytes=200)
    refs = []
    for i in range(6):
        refs += j.append([{"kind": "run", "pk": "k%d" % i, "pad": "z" * 60}])
    j.mark_projected(refs)
    j.mark_settled(refs)
    n = len(segment_paths(j))
    _age(j, 40)
    assert j.prune(datetime.now()) == 0           # durable is 0: nothing goes
    j.set_acked(6, durable=6)
    _age(j, 10)
    assert j.prune(datetime.now()) == 0           # durable, but too young
    _age(j, 40)
    gone = j.prune(datetime.now())
    assert gone >= 1 and len(segment_paths(j)) == n - gone
    expected = j.next_seq()          # 6 runs + the two marks: 9
    j.close()
    j = journal(tmp_path, segment_bytes=200)
    # the replay suppression index outlives the segments it came from
    assert all(j.known("k%d" % i) for i in range(6))
    # and numbering carries on past the pruned records
    assert j.append([{"kind": "run"}]) == ["%s:%d" % (j.epoch, expected)]
    assert expected == 9


def test_retention_never_prunes_a_reading_still_owed(tmp_path):
    j = journal(tmp_path, segment_bytes=200)
    refs = []
    for i in range(6):
        refs += j.append([{"kind": "run", "pad": "z" * 60}])
    j.mark_projected(refs)            # logged, but the result never filed
    j.set_acked(6, durable=6)
    _age(j, 40)
    j.prune(datetime.now())
    assert len(j.open_runs()) == 6


# ── disk policy ─────────────────────────────────────────────────────────────

class Disk:
    def __init__(self, free):
        self.free = free

    def __call__(self, path):
        from collections import namedtuple
        return namedtuple("du", "total used free")(10 ** 12, 0, self.free)


def test_disk_policy_warns_then_pauses_file_ingest_but_never_drops(tmp_path):
    """§3.4: warn at 200 MB unacked or 80 % of a 500 MB budget; at 1 GB
    unacked or < 1 GB free, prune durable segments early; if that is not
    enough, PAUSE FILE INGEST — the instrument file still holds those bytes.
    Serial and manual readings keep journaling: they have no other copy.
    Scaled down here by injecting the limits."""
    disk = Disk(free=10 ** 12)
    j = journal(tmp_path, disk_usage=disk,
                limits={"warn_unacked": 400, "budget": 2000, "warn_fraction": 0.8,
                        "pause_unacked": 1500, "min_free": 1000})
    assert j.enforce_disk_policy(NOW)["level"] == "ok"
    while j.disk_state()["unacked_bytes"] < 400:
        j.append([{"kind": "run", "pad": "w" * 50}])
    st = j.enforce_disk_policy(NOW)
    assert st["level"] == "warn" and not st["pause_files"]
    while j.disk_state()["unacked_bytes"] < 1500:
        j.append([{"kind": "run", "pad": "w" * 50}])
    st = j.enforce_disk_policy(NOW)
    assert st["pause_files"] and st["level"] == "pause_files"
    assert "paused" in st["message"]
    # serial keeps journaling while file ingest is paused
    pk = j.append_frame("100126-9,0.81")
    assert dict(j.pending_frames())[pk] == "100126-9,0.81"


def test_low_free_space_prunes_durable_segments_early_before_pausing(tmp_path):
    disk = Disk(free=500)
    j = journal(tmp_path, segment_bytes=200, disk_usage=disk,
                limits={"warn_unacked": 10 ** 9, "budget": 10 ** 9,
                        "warn_fraction": 0.8, "pause_unacked": 10 ** 9,
                        "min_free": 1000})
    refs = []
    for i in range(8):
        refs += j.append([{"kind": "run", "pad": "q" * 60}])
    j.mark_projected(refs)
    j.mark_settled(refs)
    j.set_acked(8, durable=8)                     # all durable, all young
    before = len(segment_paths(j))
    st = j.enforce_disk_policy(datetime.now())
    assert len(segment_paths(j)) < before         # pruned early, under 30 days
    assert st["pruned_early"] >= 1
    # still under the free-space floor (the fake disk does not move): pause
    assert st["pause_files"]


def test_unknown_free_space_is_said_not_assumed(tmp_path):
    """A failed disk_usage is not "plenty free": the state says unknown."""
    def broken(path):
        raise OSError("statvfs failed")
    j = journal(tmp_path, disk_usage=broken)
    st = j.enforce_disk_policy(NOW)
    assert st["free_bytes"] is None
    assert "unknown" in st["message"]
