"""A local copy of `lem_machine_log`, refreshed on a slow timer.

Ryan wanted the History and Logs pages to show the whole record rather than
the newest page of it, and then asked the better question: *"cant it pull it
every 5 minutes? and just keep it local?"*

**The database was never the constraint.** Measured against live LabCore on
27 Aug 2026:

    lem_machine_log                    41,903 rows
    the whole table, one read            1.00 s   18.9 MB
    one instrument's 26,106 rows         2.23 s   13.8 MB

Both are inside LabCore's 8 s read interrupt. What could not take it is the
QUEUE: LabCore serialises reads and writes through one channel at about
1.5 ops/sec (see the root CLAUDE.md), so a deep read on every History tab open,
from every screen in the lab, spends write slots the benches need. Paying it
once every five minutes instead is what turns "show me everything" from a load
problem into a feature.

**Authority does not move.** LabCore remains the record. This file is a cache,
is deletable at any moment, and rebuilds itself from scratch in about a second.
`RELEASING.md` already describes `data/` as regenerable cache and ships no
state in the release archive, so a deploy that drops it loses nothing. The
CLAUDE.md rule that nothing is measured "from local disk" is about where a
VERDICT comes from — status is still derived from `lem_machine_log` through
the gateway, exactly as before.

**The cursor is `rowid`, not `ts`.** `rowid` is visible through the gateway and
monotonic for appends. A timestamp cursor would be wrong here and quietly so:
`_audit` stamps to whole seconds, ties are ordinary (two reporting queries
already say `ORDER BY ts, rowid` because of it), and `WHERE ts > last` drops
every row that shares the last second it saw. The counts still look right
afterwards, which is what makes it the dangerous version.

**A failed pull never shrinks it.** The mirror keeps what it holds, records why
it is behind, and says so — a cache that empties itself during a blip reports a
lab with no history, and on a 17025 panel that is a statement about the record.
"""

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import List, Optional, Dict

from labcore_result import LabCoreError, LabCoreUnavailable

#: Every column the readers need, in the order the mirror stores them. `rowid`
#: rides along because it is the pull cursor; nothing on screen shows it.
COLUMNS = ("rowid", "machine_uid", "ts", "kind", "lab_id", "test_name",
           "value", "detail")

#: How often the background thread pulls. Ryan asked for five minutes and five
#: minutes is right: the incremental read is a few rows, and the pages this
#: feeds are read by people rather than polled.
REFRESH_SECONDS = 300.0

#: Rows per LabCore round trip while filling. The first fill of a big lab is
#: one read of the whole table (1.00 s measured), but a lab that has been
#: running for a year is not, and a single unbounded read is how a page ends up
#: sitting behind LabCore's 8 s interrupt with nothing to show for it.
PULL_CHUNK = 20000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS log (
  rowid_src   INTEGER PRIMARY KEY,
  machine_uid TEXT,
  ts          TEXT,
  kind        TEXT,
  lab_id      TEXT,
  test_name   TEXT,
  value       TEXT,
  detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_log_uid_ts ON log(machine_uid, ts DESC);
CREATE INDEX IF NOT EXISTS idx_log_ts ON log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_log_lab ON log(lab_id);
CREATE INDEX IF NOT EXISTS idx_log_test ON log(test_name);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _failed(detail) -> bool:
    """Did the bench record this reading as OUT OF SPEC?

    Only an explicit False counts. A detail that will not parse, or one with
    no verdict in it, is UNKNOWN and never a failure — inventing one would put
    a red reading on a panel about a row whose formatting was wrong.
    """
    if isinstance(detail, dict):
        parsed = detail
    else:
        try:
            parsed = json.loads(detail or "{}")
        except (TypeError, ValueError):
            return False
    return isinstance(parsed, dict) and parsed.get("in_spec") is False


class LogMirror:
    """`lem_machine_log`, locally, newest-first readable and walkable.

    Thread-safe by one lock around the connection: the refresher runs on a
    background thread while Flask reads on request threads, and SQLite
    connections are not shareable across threads without it.
    """

    def __init__(self, gateway, path: str) -> None:
        self.gateway = gateway
        self.path = path
        self._lock = threading.Lock()
        folder = os.path.dirname(os.path.abspath(path))
        if folder:
            os.makedirs(folder, exist_ok=True)
        # `check_same_thread=False` is safe only because every use of this
        # connection goes through `self._lock`. It is not a shortcut around
        # the threading rule; it is the threading rule moved up one level.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    # ── the pull ─────────────────────────────────────────────────────────

    def refresh(self) -> int:
        """Pull everything newer than what is held. Returns how many arrived.

        Raises whatever the gateway raises. The caller decides what a failure
        means; what this guarantees is that a failure leaves the mirror exactly
        as it was, with a reason recorded, rather than half-written or empty.
        """
        try:
            got = self._pull()
        except LabCoreError as exc:
            self._set_meta("stale_reason", str(exc) or exc.__class__.__name__)
            raise
        except Exception as exc:                       # noqa: BLE001
            self._set_meta("stale_reason", "%s: %s" % (type(exc).__name__, exc))
            raise
        self._set_meta("stale_reason", "")
        self._set_meta("filled_at", _now())
        return got

    def _pull(self) -> int:
        self._reset_if_source_changed()
        total = 0
        while True:
            since = self.max_rowid()
            res = self.gateway.read_sql(
                "SELECT rowid AS rowid_src, machine_uid, ts, kind, lab_id, "
                "test_name, value, detail FROM lem_machine_log "
                "WHERE rowid > ? ORDER BY rowid LIMIT ?", [since, PULL_CHUNK])
            rows = self._rows(res)
            if not rows:
                return total
            # Written in ONE transaction per chunk. A partial chunk on disk
            # would still advance `max_rowid` past rows that never landed, and
            # the next pull would skip them for good.
            with self._lock:
                self._db.executemany(
                    "INSERT OR REPLACE INTO log (rowid_src, machine_uid, ts, "
                    "kind, lab_id, test_name, value, detail) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [(r["rowid_src"], r["machine_uid"], r["ts"], r["kind"],
                      r["lab_id"], r["test_name"], r["value"], r["detail"])
                     for r in rows])
                self._db.commit()
            total += len(rows)
            if len(rows) < PULL_CHUNK:
                return total

    def _reset_if_source_changed(self) -> None:
        """Throw the copy away if it is a copy of a different database.

        THE CURSOR ONLY EVER GOES UP, which is exactly right while this is
        pointed at one LabCore and silently wrong the moment it is not. Found
        in a dev run: a mirror file left over from a bigger database had a
        higher `max_rowid` than the new one, so `WHERE rowid > ?` matched
        nothing, the mirror reported itself full and current, and the QC wall
        rendered "No QC has been recorded on any instrument" over a lab with
        280 QC results. Nothing anywhere would have said so.

        It happens for real whenever LabCore is restored from backup, rebuilt,
        or the server is pointed at another instance — and this feeds a wall
        display, where nobody is standing there to notice the data stopped
        moving.

        The test is simply whether the source still HAS the row we stopped at.
        An append-only log always does; a different database does not. One
        cheap indexed lookup per refresh, against being confidently wrong for
        as long as nobody restarts anything.
        """
        held = self.max_rowid()
        if not held:
            return
        res = self.gateway.read_sql(
            "SELECT COUNT(*) n FROM lem_machine_log WHERE rowid = ?", [held])
        rows = self._rows(res)
        if rows and int(list(rows[0].values())[0] or 0):
            return                       # same database, carry on incrementally
        with self._lock:
            self._db.execute("DELETE FROM log")
            self._db.commit()

    @staticmethod
    def _rows(res) -> List[dict]:
        """The gateway's answer, or the news that it was not one.

        A missing table MAY be empty — no module has ever written a log row.
        Anything else raises: an empty answer here becomes "this lab has no
        history", written to disk and then served as fact for five minutes.
        """
        if isinstance(res, dict):
            error = res.get("error")
            if error:
                if "no such table" in str(error).lower():
                    return []
                raise LabCoreUnavailable(str(error))
            rows = res.get("rows")
            if rows is None:
                raise LabCoreUnavailable(
                    "LabCore answered with no rows key while reading "
                    "lem_machine_log; that is not an empty log.")
            return [dict(r) for r in rows]
        return [dict(r) for r in (res or [])]

    # ── reading it back ──────────────────────────────────────────────────

    def events(self, machine_uid: Optional[str] = None,
               limit: Optional[int] = None,
               before: Optional[str] = None) -> List[dict]:
        """Newest first. `before` is a `ts`; rows strictly older come back.

        Strictly older, so a walk cannot show the cursor row twice. Ties on
        `ts` are broken by `rowid_src` for a stable order, and the cursor is
        compared on `ts` alone because that is what a caller can see — a page
        boundary landing inside a group of same-second rows loses the rest of
        that second, which is why callers walk by page rather than by row and
        why `limit` is a page size rather than an offset.
        """
        sql = ["SELECT rowid_src, machine_uid, ts, kind, lab_id, test_name, "
               "value, detail FROM log"]
        where, args = [], []
        if machine_uid:
            where.append("machine_uid = ?")
            args.append(machine_uid)
        if before:
            # A TIMESTAMP CANNOT ADDRESS A ROW HERE. `_audit` stamps to whole
            # seconds and an instrument reporting five cuts off one injection
            # writes five rows in the same instant, so `ts < cursor` steps over
            # every row sharing the last second of a page. Measured on the live
            # lab: a walk of Agilent GC 1 in pages of 200 reached the "start of
            # the record" having found 21,854 of 26,107 rows, with nothing on
            # screen able to show it — the pages were full and the order was
            # right.
            #
            # So the cursor is `ts|rowid`, and the comparison is the compound
            # one. A bare `ts` still works (an older client, or the first page
            # before a rowid was known) and keeps the old, lossy behaviour for
            # that one boundary rather than failing.
            ts, _, rid = str(before).partition("|")
            if rid.isdigit():
                where.append("(ts < ? OR (ts = ? AND rowid_src < ?))")
                args.extend([ts, ts, int(rid)])
            else:
                where.append("ts < ?")
                args.append(ts)
        if where:
            sql.append("WHERE " + " AND ".join(where))
        sql.append("ORDER BY ts DESC, rowid_src DESC")
        if limit is not None:
            sql.append("LIMIT ?")
            args.append(int(limit))
        with self._lock:
            cur = self._db.execute(" ".join(sql), args)
            return [dict(r) for r in cur.fetchall()]

    def by_lab_id(self, lab_id: str, limit: int = 50) -> List[dict]:
        """Every row for one sample, newest first. Local, exact, instant.

        A Lab ID is an exact key and the mirror holds the WHOLE log, so this
        answers over the entire record rather than over the newest slice of it
        — which is the difference between "this sample was never tested" and
        the truth.
        """
        with self._lock:
            cur = self._db.execute(
                "SELECT rowid_src, machine_uid, ts, kind, lab_id, test_name, "
                "value, detail FROM log WHERE lab_id = ? "
                "ORDER BY ts DESC, rowid_src DESC LIMIT ?",
                [str(lab_id), int(limit)])
            return [dict(r) for r in cur.fetchall()]

    def search(self, term: str, limit: int = 200) -> List[dict]:
        """Every row matching `term`, over the WHOLE record, newest first.

        Ryan: *"have it search through all time"*. `lab_search`'s in-memory
        index is folded from the newest `SEARCH_CORPUS_ROWS` (20,000) log rows
        — most of the table before the history import and about ten days after
        it. Anything older than that was not in the haystack, which is why a
        sample somebody had run came back as "no match".

        This searches the local copy instead, which holds every row. It is one
        indexed query against a file on this machine, so covering all time
        costs less than the ten-day window did over the network.

        WHAT IS MATCHED: lab_id, test_name, value and machine_uid. Deliberately
        not `detail` — it is a JSON blob per row and a LIKE over 214,000 of
        them turns a keystroke into a table scan, for hits nobody is looking
        for.

        `%` and `_` are escaped. A person typing one is asking for that
        character, and `_` is ordinary inside a method name; treated as a
        wildcard it silently widens the search instead of narrowing it.
        """
        term = (term or "").strip()
        if not term:
            # A blank query over the whole log is not a search result, it is a
            # denial of service with a scrollbar.
            return []
        esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = "%" + esc + "%"
        with self._lock:
            cur = self._db.execute(
                "SELECT rowid_src, machine_uid, ts, kind, lab_id, test_name, "
                "value, detail FROM log WHERE "
                "  lab_id    LIKE ? ESCAPE '\\' "
                "  OR test_name LIKE ? ESCAPE '\\' "
                "  OR value     LIKE ? ESCAPE '\\' "
                "  OR machine_uid LIKE ? ESCAPE '\\' "
                "ORDER BY ts DESC, rowid_src DESC LIMIT ?",
                [like, like, like, like, int(limit)])
            return [dict(r) for r in cur.fetchall()]

    def query(self, term: str = "", machine_uid: str = "", kind: str = "",
              since: str = "", until: str = "",
              limit: int = 500) -> List[dict]:
        """The Logs page's question, answered over the WHOLE record.

        `_log_entries` used to fetch the newest `limit` rows from LabCore and
        then grep those in Python, so a search only ever saw the page that
        happened to be fetched. Measured on the live lab: "Flash" returned 2
        events out of a 214,714-row log holding thousands of flash rows, and
        the flash instruments looked absent because their rows are not in the
        newest 500 of a lab where one instrument writes constantly.

        Here the filters go INTO the query, so narrowing by instrument narrows
        the search rather than narrowing what gets grepped.
        """
        where, args = [], []
        if machine_uid:
            where.append("machine_uid = ?")
            args.append(machine_uid)
        if kind:
            where.append("kind = ?")
            args.append(kind)
        if since:
            where.append("ts >= ?")
            args.append(since)
        if until:
            # Inclusive of the whole day when a bare date is given, the same
            # rule the LabCore path follows.
            where.append("ts < ?")
            args.append(until if len(until) > 10 else until + "T99")
        term = (term or "").strip()
        if term:
            esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = "%" + esc + "%"
            where.append("(lab_id LIKE ? ESCAPE '\\' "
                         "OR test_name LIKE ? ESCAPE '\\' "
                         "OR value LIKE ? ESCAPE '\\' "
                         "OR kind LIKE ? ESCAPE '\\' "
                         "OR detail LIKE ? ESCAPE '\\')")
            args += [like] * 5
        sql = ("SELECT rowid_src, machine_uid, ts, kind, lab_id, test_name, "
               "value, detail FROM log")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC, rowid_src DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def count(self, machine_uid: Optional[str] = None) -> int:
        sql = "SELECT COUNT(*) n FROM log"
        args: list = []
        if machine_uid:
            sql += " WHERE machine_uid = ?"
            args.append(machine_uid)
        with self._lock:
            return int(self._db.execute(sql, args).fetchone()["n"])

    def max_rowid(self) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(MAX(rowid_src), 0) m FROM log").fetchone()
            return int(row["m"])

    def latest_qc(self) -> Dict[tuple, dict]:
        """The newest QC verdict per (machine_uid, test_name), from the copy.

        WHICH STANDARD a verdict was made against is the thing the floor cannot
        otherwise know. `lem_machine_specs.last_qc_*` is the bench's own cache
        of its last verdict and `carry_last_qc` there carries it across a
        change of standard, so on 3 Sep 2026 Multitek S showed a 24-Aug AO25
        reading under AF26's band, marked in spec.

        It is answered from here rather than from the snapshot for two reasons.
        The snapshot's `event` arm is the newest 60 rows LAB-WIDE, of every
        kind, so a QC verdict from last week is simply not in it. And a
        `GROUP BY` over `lem_machine_log` in the batched read would be an
        unindexed scan of 220k rows on the SMB share, which is the exact
        pattern that trips LabCore's 8-second watchdog and blocks every write
        in the building — see LOG_INDEX_DDL on the bench. This file is local.

        KEYED BY THE STANDARD TOO — (machine_uid, test_name, lab_id) — because
        the question is not "what was measured last" but "has this instrument
        ever been checked against the standard it is assigned NOW". Keying on
        (machine, test) alone answers the first, and the first is useless here:
        Multitek S's newest verdict WAS against AF26, so a newest-only check
        found no disagreement and passed the 24-Aug AO25 reading straight
        through. That flaw survived one end-to-end run and was caught only by
        replaying real rows a second time.

        An empty answer means the copy has not filled yet, and the caller must
        treat that as UNKNOWN rather than as "no QC has ever been run".
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT machine_uid, test_name, ts, lab_id, value, detail "
                "FROM log WHERE kind = 'qc' AND test_name != '' "
                "ORDER BY ts, rowid_src").fetchall()
        out: Dict[tuple, dict] = {}
        for r in rows:
            uid, test_name, ts, lab_id, value, detail = (
                r["machine_uid"], r["test_name"], r["ts"], r["lab_id"],
                r["value"], r["detail"])
            key = (str(uid or ""), str(test_name or ""), str(lab_id or ""))
            ts = str(ts or "")
            row = {"ts": ts, "lab_id": str(lab_id or ""),
                   "value": value, "detail": detail}
            was = out.get(key)
            # Ascending, so a later timestamp simply wins.
            if was is None or ts > was["ts"]:
                out[key] = row
                continue
            # A TIE IS A BATCH, AND A FAILURE IN IT IS THE VERDICT. A bench
            # logs a whole print at one instant: Multitek NS wrote seven
            # readings at 17:05:14 on 3 Sep, six around 2.79 and one at 5.248
            # that put it on RED. Taking whichever row happened to be last
            # made the panel say "2.78 · in spec" underneath a red dot, about
            # the very batch that failed. Same precedence as everywhere else
            # here — RED outranks GREEN — so a recorded failure wins the tie.
            if ts == was["ts"] and _failed(detail) and not _failed(was["detail"]):
                out[key] = row
        return out

    def state(self) -> dict:
        """What this mirror is, so a page can say it rather than imply it."""
        return {
            "rows": self.count(),
            "max_rowid": self.max_rowid(),
            "filled_at": self._get_meta("filled_at") or None,
            "stale_reason": self._get_meta("stale_reason") or "",
        }

    # ── meta ─────────────────────────────────────────────────────────────

    def _set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)",
                [key, value])
            self._db.commit()

    def _get_meta(self, key: str) -> str:
        with self._lock:
            row = self._db.execute(
                "SELECT v FROM meta WHERE k = ?", [key]).fetchone()
            return str(row["v"]) if row else ""


class LogMirrorService:
    """The five-minute timer. Same shape as `SnapshotService`'s thread: a
    daemon that never lets an exception escape, because a refresher that dies
    silently leaves a page serving month-old rows that look current."""

    def __init__(self, mirror: LogMirror,
                 seconds: float = REFRESH_SECONDS) -> None:
        self.mirror = mirror
        self.seconds = float(seconds)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="lem-log-mirror")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.mirror.refresh()
            except Exception:                          # noqa: BLE001
                # Recorded on the mirror by `refresh` itself; the page reports
                # it. Never re-raised: this thread going down is how a stale
                # mirror stops being obvious.
                pass
            self._stop.wait(self.seconds)
