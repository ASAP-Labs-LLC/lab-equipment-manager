"""A model bench for the v2 bench API tests (transfer spec §6.1).

It speaks the protocol the way the spec says a v4 bench must, and nothing
cleverer: records numbered per epoch, each sync sends "from acked+1", and the
`acked` in ANY answer — 200 or 409 — is adopted, even when it is ahead of what
the bench believed (the N3 rule). That one rule is what the server's side has
to make safe, so the tests drive it directly.

The canonical body, the CRC and the running digest are computed here with the
exact recipe of `lem_station_module.canonical_body` / `journal_line` /
`running_digest`. `test_bench_v2_sync.py` also runs the REAL module journal
against the server, so a drift between this copy and the module fails there.
"""
import hashlib
import json
import zlib

from labcore_counter import CountingLabCore
from live_presence import LivePresence

SHARED_TOKEN = "shared-live-token"
UID = "pac-flash-1"
DIGEST_ZERO = "0" * 64


class StubAuth:
    def login(self, u, p):
        return ("ryan", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


def canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")


def with_crc(body: dict) -> dict:
    out = dict(body)
    out["crc"] = "%08x" % (zlib.crc32(canonical(body)) & 0xffffffff)
    return out


def chain(previous_hex: str, body: dict) -> str:
    return hashlib.sha256(bytes.fromhex(previous_hex)
                          + canonical(body)).hexdigest()


def seed_machine(store, uid=UID, title="PAC Flash 1"):
    """A registered machine: its configuration row, and the status row the
    floor lists machines from."""
    from snapshot_service import SCHEMA_DDL
    res = store.sql("INSERT INTO lem_machine_config (machine_uid, title, "
                    "config, updated_at) VALUES (?, ?, '{}', "
                    "'2026-10-01T08:00:00')", [uid, title])
    assert "error" not in res, res
    ddl = [s for s in SCHEMA_DDL
           if s.startswith("CREATE TABLE IF NOT EXISTS lem_machine_status ")]
    assert len(ddl) == 1
    assert "error" not in store.sql(ddl[0])
    res = store.sql("INSERT INTO lem_machine_status (machine_uid, title, "
                    "status, reason, updated_at) VALUES (?, ?, 'UNKNOWN', '', "
                    "'2026-10-01T08:00:00')", [uid, title])
    assert "error" not in res, res


def make_app(store, labcore=None, live=None):
    from web_app import create_app
    labcore = labcore if labcore is not None else CountingLabCore()
    app = create_app(store, labcore=labcore, authenticator=StubAuth(),
                     secret="s", live=live if live is not None else LivePresence(),
                     live_token=SHARED_TOKEN)
    app.config["TESTING"] = True
    return app


def enroll(client, uid=UID) -> str:
    r = client.post("/api/v2/bench/%s/enroll" % uid,
                    headers={"X-LEM-Token": SHARED_TOKEN},
                    json={"machine_uid": uid, "module_version": "4.0.0"})
    assert r.status_code == 200, (r.status_code, r.get_json())
    return r.get_json()["token"]


def run_record(seq, epoch, uid=UID, lab_id=None, value=None, lh=None):
    lab_id = lab_id if lab_id is not None else "L-%05d" % seq
    value = value if value is not None else "%.1f" % (40 + (seq % 50) * 0.1)
    return {"seq": seq, "epoch": epoch, "uid": uid, "kind": "run",
            "ts": "2026-10-01T09:%02d:%02d-07:00" % ((seq // 60) % 60, seq % 60),
            "module": "4.0.0", "src": "file", "pk": "pk-%s-%d" % (epoch, seq),
            "lh": lh or hashlib.sha256(("%s|%s" % (lab_id, value)).encode()
                                       ).hexdigest()[:32],
            "origin": "live", "lab_id": lab_id, "values": {"Flash": value},
            "raw": {}, "corrections": {}}


class Bench:
    """One bench's journal, in memory, and its uploader's one rule."""

    def __init__(self, client, token, epoch="ep-1", uid=UID):
        self.client = client
        self.token = token
        self.epoch = epoch
        self.uid = uid
        self.records = []           # every record ever journaled, seq order
        self.acked = 0
        self.answers = []
        self._digests = [DIGEST_ZERO]   # [i] = digest through seq i

    def journal(self, n=1, make=None):
        for _ in range(n):
            seq = len(self.records) + 1
            body = (make or run_record)(seq, self.epoch, uid=self.uid)
            self.records.append(body)
            self._digests.append(chain(self._digests[-1], body))
        return self

    def digest_through(self, seq):
        return self._digests[seq]

    def body(self, from_seq=None, limit=100, **extra):
        from_seq = self.acked + 1 if from_seq is None else from_seq
        recs = [with_crc(b) for b in self.records[from_seq - 1:
                                                  from_seq - 1 + limit]]
        doc = {"machine_uid": self.uid, "epoch": self.epoch, "proto": 2,
               "module_version": "4.0.0", "from_seq": from_seq,
               "records": recs,
               "live": {"status": "GREEN", "reason": "",
                        "at": "2026-10-01T09:00:00-07:00",
                        "interval_seconds": 30},
               "stats": {"unacked": len(self.records) - self.acked,
                         "road": "public",
                         "digest": self.digest_through(from_seq - 1),
                         "labcore": {"reads": 0, "writes": 0, "failed": 0,
                                     "timeouts": 0, "watchdog": 0}}}
        doc.update(extra)
        return doc

    def post_doc(self, doc, token=None):
        return self.client.post(
            "/api/v2/bench/%s/sync" % self.uid, json=doc,
            headers={"X-LEM-Bench-Token": self.token if token is None else token,
                     "User-Agent": "LEM-Station/4.0.0 (%s)" % self.uid,
                     "X-LEM-Proto": "2"})

    def sync(self, from_seq=None, limit=100, lose_response=False, **extra):
        """One sync. With `lose_response` the server runs it and the bench
        never sees the answer — N3's shape."""
        r = self.post_doc(self.body(from_seq, limit, **extra))
        self.answers.append(r)
        if lose_response:
            return None
        if r.status_code in (200, 409):
            self.acked = int(r.get_json()["acked"])     # the N3 rule
        return r

    def drain(self, limit=100, max_rounds=10000):
        for _ in range(max_rounds):
            if self.acked >= len(self.records):
                return
            r = self.sync(limit=limit)
            assert r.status_code in (200, 409), (r.status_code, r.get_json())
        raise AssertionError("did not drain")


def stored_seqs(store, uid=UID):
    """(epoch, seq) of every bench record the store holds, with repeats — a
    duplicate shows as a repeat. `bench_record` is the custody table: every
    record a bench sends is held there exactly once, whatever its kind."""
    res = store.read_sql("SELECT bench_epoch, bench_seq FROM bench_record "
                         "WHERE machine_uid = ?", [uid])
    assert "error" not in res, res
    return [(r["bench_epoch"], r["bench_seq"]) for r in res["rows"]]


def log_custody_seqs(store, uid=UID):
    """(epoch, seq) of every machine-log row that carries a bench record's
    identity, with repeats. A record that becomes log rows has exactly ONE
    such row (the unique index allows no more), so a repeat here is a
    duplicate in the 17025 record itself."""
    res = store.read_sql(
        # raw-log: custody counts every row, hidden or not
        "SELECT bench_epoch, bench_seq FROM lem_machine_log "
        "WHERE machine_uid = ? AND bench_seq IS NOT NULL", [uid])
    assert "error" not in res, res
    return [(r["bench_epoch"], r["bench_seq"]) for r in res["rows"]]


def dup_and_lost(sent, stored):
    """(lost, dup) between two multisets of keys — the §9.4 tally, with no
    term zeroed: lost = Σ max(0, sent − stored), dup = Σ max(0, stored − sent)."""
    from collections import Counter
    s, t = Counter(sent), Counter(stored)
    lost = sum(max(0, n - t[k]) for k, n in s.items())
    dup = sum(max(0, n - s[k]) for k, n in t.items())
    return lost, dup


def log_rows(store, uid=UID):
    res = store.read_sql(
        # raw-log: the test reads every row it wrote
        "SELECT * FROM lem_machine_log WHERE machine_uid = ? ORDER BY id", [uid])
    assert "error" not in res, res
    return res["rows"]
