#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
custody.py — backups of LEM's store, and the proof that they are backups
(transfer spec §11, §3.4; piece T-P11).

Moving the 17025 record out of LabCore moved it out of LabCore's backups as
well. Until this module, `lem.db` was one SQLite file on one disk, and §11 says
what that means in so many words: until the off-host copy is verified, the
record is LESS protected in the store than it was inside LabCore. This module
is the protection, in five parts.

1. **Hourly online backup.** SQLite's backup API copies a consistent snapshot
   while the store keeps taking writes. `PRAGMA integrity_check` runs on the
   COPY and decides whether it counts; a copy that fails is deleted and the
   failure is said (`last_backup_ok = 0`, red in the global status). Kept: 48
   hourly, the newest of each of 35 days, the newest of each of 24 months.
   Pre-migration copies are never pruned.

2. **`durable_seq`, read out of the backup.** A bench deletes journal segments
   only at or below `durable` (and 30 days old, §3.4), so `durable` must mean
   "inside a completed, checked backup" — not "received". It is therefore read
   from the copy's own `bench_cursor`, never from the live store: a record that
   arrived while the copy was being made is on one disk only, and the bench
   must keep it.

3. **A manifest that can catch tampering.** Beside every copy:
   per-day row count and SHA-256 over that day's `lem_machine_log` rows by id
   (the spec's `log_digest`, also written into the store), a digest of every
   annotation (an annotation can hide a row), each table's row count, and each
   bench epoch's running digest recomputed from what the bench sent. `verify`
   recomputes all of it from a scratch COPY of the file — backups are copied,
   never opened in place — and names the day or epoch that differs. A restore
   refuses a file that does not re-verify.

4. **Off-host, nightly, checked at the target.** The newest backup is copied
   to `LEM_BACKUP_OFFSITE` (Ryan names it: D5) and its SHA-256 re-read THERE.
   `offsite_last_ok` is what the bridge-off button reads: older than 26 h, or
   missing, and the bridge stays on (§11, the custody shift).

5. **A monthly restore drill.** The newest backup is restored to a scratch
   folder (re-verifying its manifest), a real LEM is booted on a scratch port
   against it READ-ONLY — the updater's candidate-boot shape, so the drill
   cannot write — `/healthz` must answer with the restored store, its guards
   intact and its schema version the manifest's, and the file must come back
   byte-identical. The result goes into `store_meta.drill_*`; Settings shows it.

Per-epoch reconciliation rides on the backup: for each bench epoch the copy
holds, LEM's records must be contiguous, must reproduce LEM's own running
digest, must not outnumber what the bench says it has journaled, and the
bench's last reported digest must have agreed. Any of those failing is a red
global-status line naming the bench.

A FAILED READ IS NEVER AN EMPTY RESULT. A backup folder that cannot be listed
is an error, not "no backups"; a state that cannot be read refuses bridge-off.

Command line (the server must be STOPPED for a restore)::

    python custody.py backup   [--store PATH] [--backup-dir DIR]
    python custody.py verify   BACKUP.db
    python custody.py restore  BACKUP.db [--store PATH]
    python custody.py list     [--backup-dir DIR]
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

FORMAT = "lem-backup/1"
KEEP_HOURLY, KEEP_DAILY, KEEP_MONTHLY = 48, 35, 24

BACKUP_EVERY = timedelta(hours=1)
BACKUP_RETRY = timedelta(minutes=5)
#: §11: the global status turns amber when the newest backup is 2 h old.
BACKUP_AMBER = timedelta(hours=2)
#: §11: off-host amber at 26 h, and the bridge-off refusal at the same age.
OFFSITE_MAX = timedelta(hours=26)
#: Nightly: inside the night window once it is 20 h since the last copy, and
#: at any hour once it is a full day (a server that was off overnight).
OFFSITE_NIGHT_AFTER = timedelta(hours=20)
OFFSITE_DAY = timedelta(hours=24)
OFFSITE_NIGHT_HOURS = range(1, 6)                  # local 01:00–05:59
OFFSITE_RETRY = timedelta(minutes=30)
DRILL_EVERY = timedelta(days=30)
DRILL_RETRY = timedelta(hours=24)
DRILL_AMBER = timedelta(days=35)
#: The drill's scratch port. The updater's candidate boot uses 15557.
DEFAULT_DRILL_PORT = 15558
DRILL_BOOT_TIMEOUT_S = 90.0

DIGEST_ZERO = "0" * 64
_NAME_RE = re.compile(
    r"^lem-(\d{8}T\d{6}Z)(-premigration)?(?:-(\d+))?\.db$")
_MANIFEST_SUFFIX = ".manifest.json"

HREF = "/settings#backups"
LINK = "Open Backups"

#: store_meta keys this module owns.
META_KEYS = ("last_backup_at", "last_backup_ok", "last_backup_file",
             "last_backup_error", "offsite_last_ok", "offsite_last_file",
             "offsite_last_error", "offsite_last_attempt", "drill_at",
             "drill_ok", "drill_backup", "drill_detail", "drill_rto_s",
             "reconcile", "reconcile_at", "bridge", "bridge_changed_at",
             "bridge_changed_by", "bridge_history")


class BackupFailed(Exception):
    """A copy that does not count as a backup, with the reason."""


class RestoreRefused(Exception):
    def __init__(self, problems: List[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


# ── small helpers ────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") \
        if dt.tzinfo else dt.isoformat(timespec="seconds")


def _parse(value) -> Optional[datetime]:
    """An aware datetime, or None when the text is missing or not a time."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")


def chain(previous_hex: str, raw: bytes) -> str:
    """The bench's running digest step (bench_api.chain, the module's
    running_digest): sha256(previous || canonical body)."""
    return hashlib.sha256(bytes.fromhex(previous_hex) + raw).hexdigest()


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _fsync_file(path: str) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: str) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def manifest_path(db_path: str) -> str:
    return str(db_path)[:-3] + _MANIFEST_SUFFIX if str(db_path).endswith(".db") \
        else str(db_path) + _MANIFEST_SUFFIX


def read_manifest(db_path: str) -> dict:
    with open(manifest_path(db_path), "r", encoding="utf-8") as f:
        man = json.load(f)
    if not isinstance(man, dict) or man.get("format") != FORMAT:
        raise ValueError("not a LEM backup manifest")
    return man


def _write_manifest(db_path: str, man: dict) -> None:
    target = manifest_path(db_path)
    tmp = target + ".partial"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(man, f, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)


def default_backup_dir(store_path: str) -> str:
    """`LEM_BACKUP_DIR`, else `backup` beside the store's folder when that
    folder is called `store` (production: C:\\ASAPApps\\lem\\store\\lem.db →
    C:\\ASAPApps\\lem\\backup), else `backup` inside the store's folder."""
    configured = os.environ.get("LEM_BACKUP_DIR", "").strip()
    if configured:
        return configured
    folder = os.path.dirname(os.path.abspath(store_path))
    if os.path.basename(folder).lower() == "store":
        return os.path.join(os.path.dirname(folder), "backup")
    return os.path.join(folder, "backup")


def default_offsite_dir() -> Optional[str]:
    """`LEM_BACKUP_OFFSITE`, the off-host target Ryan names (D5). There is no
    default on purpose: a guessed share that is not carried off site would be
    a green light over a single copy."""
    return os.environ.get("LEM_BACKUP_OFFSITE", "").strip() or None


def _ro_uri(path: str) -> str:
    return "file:{0}?mode=ro".format(
        os.path.abspath(path).replace("?", "%3f").replace("#", "%23"))


# ── what a copy contains ─────────────────────────────────────────────────────

def _integrity(con) -> str:
    rows = con.execute("PRAGMA integrity_check").fetchall()
    return "\n".join(str(r[0]) for r in rows)


def _tables(con) -> List[str]:
    return [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT "
        "LIKE 'sqlite_%' ORDER BY name")]


def _day(ts) -> str:
    text = str(ts or "")
    return text[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", text) else "undated"


def describe(con) -> dict:
    """Everything the manifest records about a database: counts, the daily
    log digest, the annotation digest, every bench epoch recomputed, and the
    `durable` candidates. Pure over the connection; never writes."""
    tables = _tables(con)
    out: Dict[str, Any] = {"tables": {}, "log_digest": {}, "annotations": None,
                           "epochs": [], "durable": [], "titles": {},
                           "schema_version": None}
    for t in tables:
        out["tables"][t] = con.execute(
            'SELECT COUNT(*) FROM "%s"' % t.replace('"', '""')).fetchone()[0]
    if "store_meta" in tables:
        row = con.execute("SELECT value FROM store_meta WHERE key = "
                          "'schema_version'").fetchone()
        out["schema_version"] = row[0] if row else None
    if "lem_machine_log" in tables:
        # raw-log: audit — the digest covers EVERY row, hidden ones included
        cur = con.execute("SELECT * FROM lem_machine_log ORDER BY rowid")
        cols = [d[0] for d in cur.description]
        ts_i = cols.index("ts") if "ts" in cols else None
        hashers: Dict[str, Any] = {}
        counts: Dict[str, int] = {}
        for row in cur:
            day = _day(row[ts_i] if ts_i is not None else None)
            h = hashers.get(day)
            if h is None:
                h = hashers[day] = hashlib.sha256(canonical(cols) + b"\n")
            h.update(canonical(list(row)) + b"\n")
            counts[day] = counts.get(day, 0) + 1
        out["log_digest"] = {d: {"rows": counts[d], "sha256": hashers[d].hexdigest()}
                             for d in sorted(counts)}
    if "log_annotation" in tables:
        h = hashlib.sha256()
        n = 0
        for row in con.execute("SELECT * FROM log_annotation ORDER BY rowid"):
            h.update(canonical(list(row)) + b"\n")
            n += 1
        out["annotations"] = {"rows": n, "sha256": h.hexdigest()}
    if "lem_machine_config" in tables:
        out["titles"] = {r[0]: r[1] for r in con.execute(
            "SELECT machine_uid, title FROM lem_machine_config")}
    if "bench_cursor" in tables and "bench_record" in tables:
        cursors = con.execute(
            "SELECT machine_uid, bench_epoch, acked_seq, durable_seq, digest, "
            "records_total, digest_mismatch FROM bench_cursor "
            "ORDER BY machine_uid, bench_epoch").fetchall()
        for uid, epoch, acked, durable, digest, total, mismatch in cursors:
            acked = int(acked or 0)
            d, held, expect, gaps, max_seq = DIGEST_ZERO, 0, 1, [], 0
            for seq, body in con.execute(
                    "SELECT bench_seq, body FROM bench_record WHERE "
                    "machine_uid = ? AND bench_epoch = ? ORDER BY bench_seq",
                    [uid, epoch]):
                held += 1
                max_seq = seq
                if seq != expect and len(gaps) < 3:
                    gaps.append(expect)
                expect = seq + 1
                if seq <= acked:
                    d = chain(d, str(body).encode("utf-8"))
            problems = []
            if held != acked or max_seq != acked or gaps:
                problems.append(
                    "LEM holds %d record(s) of this epoch up to seq %d, but "
                    "its cursor says 1..%d%s" % (
                        held, max_seq, acked,
                        (" (missing from seq %d)" % gaps[0]) if gaps else ""))
            if (digest or DIGEST_ZERO) != d:
                problems.append(
                    "LEM's stored records no longer reproduce its own running "
                    "digest through seq %d: a stored record was changed" % acked)
            if total is not None and int(total) < acked:
                problems.append(
                    "the bench counts %d record(s) in this epoch, and LEM "
                    "holds %d" % (int(total), acked))
            if mismatch:
                try:
                    at = json.loads(mismatch).get("through")
                except (ValueError, AttributeError):
                    at = None
                problems.append(
                    "the bench's journal and LEM disagreed at or before seq %s"
                    % (at if at is not None else "?"))
            out["epochs"].append({
                "machine_uid": uid, "epoch": epoch, "acked": acked,
                "held": held, "records_total": (int(total) if total is not None
                                                else None),
                "cursor_digest": digest, "recomputed": d,
                "ok": not problems, "problems": problems})
            out["durable"].append({"machine_uid": uid, "epoch": epoch,
                                   "acked": acked})
    return out


def reconcile_of(desc: dict) -> List[dict]:
    """The per-epoch reconciliation, as Settings and the status show it."""
    return [{k: e[k] for k in ("machine_uid", "epoch", "acked", "held",
                               "records_total", "ok", "problems")}
            for e in desc.get("epochs") or []]


# ── taking a backup ──────────────────────────────────────────────────────────

def _backup_name(backup_dir: str, now: datetime, kind: str) -> str:
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = "lem-%s%s" % (stamp, "-premigration" if kind == "pre_migration" else "")
    name, n = base + ".db", 1
    while os.path.exists(os.path.join(backup_dir, name)) or os.path.exists(
            os.path.join(backup_dir, name + ".partial")):
        n += 1
        name = "%s-%d.db" % (base, n)
    return name


def take_backup(store_path: str, backup_dir: str, now: datetime,
                kind: str = "hourly",
                after_copy: Optional[Callable[[], Any]] = None) -> dict:
    """Copy the store into `backup_dir`, check the copy, write its manifest.

    Returns {"path", "manifest"}. Raises (BackupFailed, OSError,
    sqlite3.Error) and leaves nothing that looks like a backup behind: the
    manifest is written LAST, and `list_backups` sees only files that have one.
    """
    os.makedirs(backup_dir, exist_ok=True)
    name = _backup_name(backup_dir, now, kind)
    final = os.path.join(backup_dir, name)
    partial = final + ".partial"
    src = dst = None
    try:
        src = sqlite3.connect(_ro_uri(store_path), uri=True)
        dst = sqlite3.connect(partial)
        src.backup(dst)
        src.close()
        src = None
        if after_copy is not None:
            after_copy()
        # One self-contained file: a WAL-mode copy would need its -wal beside
        # it, and a backup that is two files is one file away from broken.
        dst.execute("PRAGMA journal_mode=DELETE")
        integ = _integrity(dst)
        if integ.strip() != "ok":
            raise BackupFailed("integrity_check on the copy did not answer "
                               "ok: " + integ.strip()[:400])
        desc = describe(dst)
        dst.close()
        dst = None
        _fsync_file(partial)
        man = dict(desc, format=FORMAT, file=name, kind=kind,
                   created_at=_iso(now), source=os.path.abspath(store_path),
                   integrity_check="ok", bytes=os.path.getsize(partial),
                   sha256=file_sha256(partial))
        os.replace(partial, final)
        _write_manifest(final, man)
        _fsync_dir(backup_dir)
        return {"path": final, "manifest": man}
    except BaseException:
        for con in (src, dst):
            if con is not None:
                try:
                    con.close()
                except sqlite3.Error:
                    pass
        for leftover in (partial, final):
            if os.path.exists(leftover) and not os.path.exists(
                    manifest_path(final)):
                try:
                    os.remove(leftover)
                except OSError:
                    pass
        raise


def pre_migration_backup(store_path: str, found_version) -> dict:
    """Called by the store before it migrates a file whose schema is older
    than this code's (§5.1: migrations run after an automatic pre-migration
    backup). Raises if the backup cannot be taken: a migration with no copy
    behind it is the one step that must not go ahead."""
    out = take_backup(store_path, default_backup_dir(store_path), _utcnow(),
                      kind="pre_migration")
    logger.warning("custody: pre-migration backup of schema %s taken: %s",
                   found_version, out["path"])
    return out


# ── listing and retention ────────────────────────────────────────────────────

def list_backups(backup_dir: str) -> List[dict]:
    """Every backup in the folder that has a manifest, newest first.

    A folder that does not exist yet holds no backups (nothing was ever
    written there). A folder that exists and cannot be listed raises:
    that is not "no backups"."""
    if not os.path.exists(backup_dir):
        return []
    names = os.listdir(backup_dir)
    out = []
    for name in names:
        m = _NAME_RE.match(name)
        if not m:
            continue
        path = os.path.join(backup_dir, name)
        if not os.path.exists(manifest_path(path)):
            continue
        at = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc)
        out.append({"name": name, "path": path, "at": at,
                    "kind": "pre_migration" if m.group(2) else "hourly",
                    "n": int(m.group(3) or 1)})
    out.sort(key=lambda b: (b["at"], b["n"]), reverse=True)
    return out


def list_backups_safe(backup_dir: str) -> List[dict]:
    try:
        return list_backups(backup_dir)
    except OSError:
        return []


def retention_keep(items: List[dict]) -> List[dict]:
    """Which backups to keep: the 48 newest, the newest of each of the 35
    newest days, the newest of each of the 24 newest months (UTC), and every
    pre-migration copy. `items` need `at` and `kind`."""
    regular = sorted((i for i in items if i.get("kind") != "pre_migration"),
                     key=lambda i: i["at"], reverse=True)
    keep = {id(i) for i in regular[:KEEP_HOURLY]}
    days: Dict[Any, dict] = {}
    months: Dict[Any, dict] = {}
    for i in regular:
        days.setdefault(i["at"].date(), i)
        months.setdefault((i["at"].year, i["at"].month), i)
    for _d, i in sorted(days.items(), key=lambda kv: kv[0],
                        reverse=True)[:KEEP_DAILY]:
        keep.add(id(i))
    for _m, i in sorted(months.items(), key=lambda kv: kv[0],
                        reverse=True)[:KEEP_MONTHLY]:
        keep.add(id(i))
    return [i for i in items if i.get("kind") == "pre_migration"
            or id(i) in keep]


def prune_backups(backup_dir: str) -> List[str]:
    items = list_backups(backup_dir)
    keep = {i["path"] for i in retention_keep(items)}
    removed = []
    for i in items:
        if i["path"] in keep:
            continue
        # the manifest first: a db without one is not a backup any more,
        # so a crash between the two removals leaves nothing half-listed
        for p in (manifest_path(i["path"]), i["path"]):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        removed.append(i["name"])
    return removed


# ── re-verification and restore ──────────────────────────────────────────────

def verify(path: str, scratch_dir: Optional[str] = None) -> dict:
    """Re-verify a backup against its manifest, on a scratch COPY.

    {"ok", "problems": [sentences], "manifest"}. Every difference is named:
    the day whose log rows changed, the table whose count changed, the bench
    epoch whose records no longer reproduce their digest."""
    problems: List[str] = []
    try:
        man = read_manifest(path)
    except (OSError, ValueError) as exc:
        return {"ok": False, "manifest": None,
                "problems": ["no readable manifest beside %s (%s): a backup "
                             "that cannot be checked is not a backup"
                             % (os.path.basename(path), exc)]}
    try:
        sha = file_sha256(path)
    except OSError as exc:
        return {"ok": False, "manifest": man,
                "problems": ["the backup file could not be read: %s" % exc]}
    if sha != man.get("sha256"):
        problems.append("the file's SHA-256 differs from its manifest: it "
                        "changed after it was written")
    tmp = tempfile.mkdtemp(prefix="lem-verify-", dir=scratch_dir)
    try:
        copy = os.path.join(tmp, "copy.db")
        shutil.copyfile(path, copy)
        con = sqlite3.connect(copy)
        try:
            integ = _integrity(con).strip()
            if integ != "ok":
                problems.append("integrity_check: " + integ[:300])
                desc = None
            else:
                desc = describe(con)
        finally:
            con.close()
    except (OSError, sqlite3.Error) as exc:
        problems.append("the copy could not be opened: %s" % exc)
        desc = None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if desc is not None:
        problems += _compare(man, desc)
    return {"ok": not problems, "problems": problems, "manifest": man}


def _compare(man: dict, desc: dict) -> List[str]:
    out = []
    want, have = man.get("log_digest") or {}, desc.get("log_digest") or {}
    for day in sorted(set(want) | set(have)):
        w, h = want.get(day), have.get(day)
        if w == h:
            continue
        if w is None:
            out.append("lem_machine_log on %s: %d row(s) the manifest does not "
                       "have" % (day, h["rows"]))
        elif h is None:
            out.append("lem_machine_log on %s: the manifest's %d row(s) are "
                       "gone" % (day, w["rows"]))
        else:
            out.append("lem_machine_log on %s: %d row(s), the manifest says %d; "
                       "the day's digest differs" % (day, h["rows"], w["rows"]))
    if (man.get("annotations") or None) != (desc.get("annotations") or None):
        out.append("log_annotation differs from the manifest (an annotation "
                   "can hide a row)")
    tw, th = man.get("tables") or {}, desc.get("tables") or {}
    for t in sorted(set(tw) | set(th)):
        if tw.get(t) != th.get(t):
            out.append("table %s: %s row(s), the manifest says %s"
                       % (t, th.get(t, "no"), tw.get(t, "none")))
    ew = {(e["machine_uid"], e["epoch"]): e for e in man.get("epochs") or []}
    eh = {(e["machine_uid"], e["epoch"]): e for e in desc.get("epochs") or []}
    for key in sorted(set(ew) | set(eh)):
        w, h = ew.get(key), eh.get(key)
        if w is None or h is None or w.get("recomputed") != h.get("recomputed") \
                or w.get("held") != h.get("held"):
            out.append("bench_record for %s epoch %s: what the bench sent no "
                       "longer matches the manifest" % key)
    if str(man.get("schema_version")) != str(desc.get("schema_version")):
        out.append("schema_version %s, the manifest says %s"
                   % (desc.get("schema_version"), man.get("schema_version")))
    return out


def restore(backup_path: str, store_path: str,
            now: Optional[datetime] = None) -> dict:
    """Put a verified backup where the store is. THE SERVER MUST BE STOPPED.

    The file it replaces is kept beside it (`.before-restore-<stamp>`, with
    its -wal and -shm), never deleted. Refuses (RestoreRefused) a backup that
    does not re-verify, and touches nothing when it does."""
    v = verify(backup_path, scratch_dir=os.path.dirname(os.path.abspath(
        store_path)) or None)
    if not v["ok"]:
        raise RestoreRefused(v["problems"])
    now = now or _utcnow()
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    kept = None
    if os.path.exists(store_path):
        kept = "%s.before-restore-%s" % (store_path, stamp)
        n = 1
        while os.path.exists(kept):
            n += 1
            kept = "%s.before-restore-%s-%d" % (store_path, stamp, n)
        os.replace(store_path, kept)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(store_path + suffix):
                os.replace(store_path + suffix, kept + suffix)
    tmp = store_path + ".restoring"
    shutil.copyfile(backup_path, tmp)
    _fsync_file(tmp)
    os.replace(tmp, store_path)
    _fsync_dir(os.path.dirname(os.path.abspath(store_path)))
    return {"ok": True, "kept": kept, "backup": backup_path,
            "manifest": v["manifest"], "problems": []}


# ── the drill's server ───────────────────────────────────────────────────────

def _port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _get_json(url: str, timeout: float = 3.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:      # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def boot_and_ask(store_path: str, port: int, scratch: str,
                 timeout: float = DRILL_BOOT_TIMEOUT_S) -> dict:
    """Boot this release's web_server.pyw on `port` against `store_path`
    exactly as the updater boots a candidate (--no-publish: the store opens
    READ-ONLY), ask /healthz, stop it. LabCore is pointed at a closed local
    port: the drill is about the store, and asks LabCore nothing.

    {"ok", "healthz", "seconds", "problem"}."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "web_server.pyw")
    env = dict(os.environ)
    env.update({"LEM_STORE_PATH": store_path,
                "LABCORE_URL": "http://127.0.0.1:9",
                "LEM_DATA_DIR": os.path.join(scratch, "data"),
                "PYTHONDONTWRITEBYTECODE": "1"})
    env.pop("LEM_BACKUP_OFFSITE", None)
    os.makedirs(env["LEM_DATA_DIR"], exist_ok=True)
    cmd = [sys.executable, script, "--no-publish", "--no-tray", "--no-reload",
           "--host", "127.0.0.1", "--port", str(int(port))]
    log_path = os.path.join(scratch, "drill-server.log")
    started = time.monotonic()
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(cmd, env=env, stdout=log,
                                stderr=subprocess.STDOUT,
                                cwd=os.path.dirname(script))
        try:
            last = ""
            while time.monotonic() - started < timeout:
                if proc.poll() is not None:
                    break
                try:
                    h = _get_json("http://127.0.0.1:%d/healthz" % int(port))
                    return {"ok": True, "healthz": h,
                            "seconds": round(time.monotonic() - started, 2),
                            "problem": None}
                except Exception as exc:               # noqa: BLE001
                    last = str(exc)
                    time.sleep(0.25)
            tail = ""
            try:
                with open(log_path, "rb") as f:
                    tail = f.read()[-400:].decode("utf-8", "replace")
            except OSError:
                pass
            why = ("exited with code %s" % proc.returncode
                   if proc.poll() is not None
                   else "did not answer /healthz within %d s (%s)"
                   % (timeout, last[:120]))
            return {"ok": False, "healthz": None,
                    "seconds": round(time.monotonic() - started, 2),
                    "problem": "the scratch server on port %d %s%s" % (
                        int(port), why, (": " + tail.strip()) if tail.strip()
                        else "")}
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=15)


# ── the service ──────────────────────────────────────────────────────────────

def _hours(td: timedelta) -> int:
    return int(td.total_seconds() // 3600)


class Custody:
    """Backups, off-host copies, the drill, reconciliation, and the bridge
    switch's custody condition, for one store. `tick()` does whatever is due;
    `start()` runs it in a daemon thread (the server's boot, never the app
    factory). State lives in `store_meta` and is mirrored in memory, so
    /healthz and the global status read it without touching the disk."""

    def __init__(self, store, backup_dir: Optional[str] = None,
                 offsite_dir: Optional[str] = "env",
                 clock: Callable[[], datetime] = _utcnow,
                 drill_dir: Optional[str] = None,
                 drill_port: Optional[int] = None) -> None:
        self.store = store
        path = getattr(store, "path", None) or ""
        self.store_path = path
        self.backup_dir = backup_dir or default_backup_dir(path)
        self.offsite_dir = (default_offsite_dir() if offsite_dir == "env"
                            else offsite_dir)
        self.drill_dir = drill_dir or os.path.join(
            os.path.dirname(os.path.abspath(self.backup_dir)), "drill")
        self.drill_port = int(drill_port or os.environ.get(
            "LEM_DRILL_PORT") or DEFAULT_DRILL_PORT)
        self.clock = clock
        self.read_only = bool(getattr(store, "read_only", False))
        #: Scheduled by the server's boot (`start`); the drill boots a second
        #: server, so only a real deployment schedules it.
        self.schedule_drill = False
        self.active = False
        self._lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._tried: Dict[str, datetime] = {}
        self._drill_lock = threading.Lock()
        self.state: Dict[str, Any] = {k: None for k in META_KEYS}
        self.state["read_error"] = None

    # ── state ──
    def hydrate(self) -> None:
        res = self.store.read_sql(
            "SELECT key, value FROM store_meta WHERE key IN (%s)"
            % ",".join("?" * len(META_KEYS)), list(META_KEYS))
        with self._lock:
            if not isinstance(res, dict) or res.get("error") or "rows" not in res:
                self.state["read_error"] = str((res or {}).get("error")
                                               if isinstance(res, dict) else res)
                return
            self.state = {k: None for k in META_KEYS}
            self.state["read_error"] = None
            for r in res["rows"]:
                self.state[r["key"]] = r["value"]

    def _set(self, values: Dict[str, Any]) -> Optional[str]:
        """Write store_meta keys (one transaction) and mirror them. Returns
        the store's error text, or None."""
        err = None
        try:
            with self.store.transaction():
                for k, v in values.items():
                    res = self.store.sql(
                        "INSERT INTO store_meta (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        [k, None if v is None else str(v)])
                    if res.get("error"):
                        raise RuntimeError(res["error"])
        except Exception as exc:                       # noqa: BLE001
            err = str(exc)
            logger.warning("custody: could not record %s: %s",
                           sorted(values), err)
        with self._lock:
            for k, v in values.items():
                self.state[k] = None if v is None else str(v)
        return err

    def _flag(self, key: str) -> Optional[bool]:
        v = self.state.get(key)
        return None if v is None else str(v) == "1"

    # ── the backup ──
    def backup_now(self, kind: str = "hourly",
                   _after_copy: Optional[Callable[[], Any]] = None) -> dict:
        if self.read_only:
            return {"ok": False, "error": "the store is open read-only (a "
                                          "candidate boot takes no backup)"}
        with self._run_lock:
            now = self.clock()
            self._tried["backup"] = now
            try:
                res = take_backup(self.store_path, self.backup_dir, now, kind,
                                  after_copy=_after_copy)
            except (BackupFailed, OSError, sqlite3.Error, ValueError) as exc:
                text = str(exc) or type(exc).__name__
                self._set({"last_backup_at": _iso(now), "last_backup_ok": "0",
                           "last_backup_error": text[:500]})
                logger.warning("custody: backup failed: %s", text)
                return {"ok": False, "error": text}
            man = res["manifest"]
            published = self.publish_durable(man)
            rec = reconcile_of(man)
            self._set({"last_backup_at": _iso(now), "last_backup_ok": "1",
                       "last_backup_file": man["file"],
                       "last_backup_error": None,
                       "reconcile": json.dumps({"epochs": rec,
                                                "titles": man.get("titles")}),
                       "reconcile_at": _iso(now)})
            try:
                removed = prune_backups(self.backup_dir)
            except OSError as exc:
                removed = []
                logger.warning("custody: pruning backups failed: %s", exc)
            return {"ok": True, "path": res["path"], "manifest": man,
                    "durable": published, "reconcile": rec,
                    "pruned": removed}

    def publish_durable(self, man: dict) -> Optional[str]:
        """`durable_seq` per (uid, epoch) = the acked the BACKUP holds, never
        lowered by an older backup and never past what the store has acked;
        and the day digests, in the same transaction. Returns an error text
        or None."""
        try:
            with self.store.transaction():
                for d in man.get("durable") or []:
                    res = self.store.sql(
                        "UPDATE bench_cursor SET durable_seq = MAX(durable_seq,"
                        " MIN(?, acked_seq)) WHERE machine_uid = ? AND "
                        "bench_epoch = ?",
                        [int(d["acked"]), d["machine_uid"], d["epoch"]])
                    if res.get("error"):
                        raise RuntimeError(res["error"])
                stamp = man.get("created_at") or _iso(self.clock())
                for day, v in (man.get("log_digest") or {}).items():
                    res = self.store.sql(
                        "INSERT INTO log_digest (day, rows, sha256, "
                        "computed_at) VALUES (?, ?, ?, ?) ON CONFLICT(day) DO "
                        "UPDATE SET rows = excluded.rows, sha256 = "
                        "excluded.sha256, computed_at = excluded.computed_at",
                        [day, int(v["rows"]), v["sha256"], stamp])
                    if res.get("error"):
                        raise RuntimeError(res["error"])
        except Exception as exc:                       # noqa: BLE001
            logger.warning("custody: durable not published: %s", exc)
            return str(exc)
        return None

    # ── off-host ──
    def offsite_now(self) -> dict:
        if self.read_only:
            return {"ok": False, "error": "the store is open read-only"}
        now = self.clock()
        self._tried["offsite"] = now
        if not self.offsite_dir:
            return self._offsite_failed(now, "no off-host target is named "
                                             "(set LEM_BACKUP_OFFSITE)")
        try:
            newest = list_backups(self.backup_dir)
        except OSError as exc:
            return self._offsite_failed(now, "the backup folder could not be "
                                             "read: %s" % exc)
        newest = [b for b in newest if b["kind"] != "pre_migration"]
        if not newest:
            return self._offsite_failed(now, "there is no backup to copy yet")
        b = newest[0]
        dst = os.path.join(self.offsite_dir, b["name"])
        tmp = dst + ".partial"
        try:
            man = read_manifest(b["path"])
            os.makedirs(self.offsite_dir, exist_ok=True)
            shutil.copyfile(b["path"], tmp)
            _fsync_file(tmp)
            got = file_sha256(tmp)
            if got != man["sha256"]:
                raise BackupFailed(
                    "the copy at the target has a different SHA-256 from the "
                    "backup (%s… against %s…): it was damaged on the way"
                    % (got[:12], man["sha256"][:12]))
            os.replace(tmp, dst)
            mtmp = manifest_path(dst) + ".partial"
            shutil.copyfile(manifest_path(b["path"]), mtmp)
            _fsync_file(mtmp)
            os.replace(mtmp, manifest_path(dst))
            _fsync_dir(self.offsite_dir)
        except (OSError, ValueError, BackupFailed) as exc:
            for p in (tmp, manifest_path(dst) + ".partial"):
                try:
                    os.remove(p)
                except OSError:
                    pass
            return self._offsite_failed(now, str(exc) or type(exc).__name__)
        try:
            prune_backups(self.offsite_dir)
        except OSError as exc:
            logger.warning("custody: pruning off-host copies failed: %s", exc)
        self._set({"offsite_last_ok": _iso(now), "offsite_last_file": b["name"],
                   "offsite_last_error": None,
                   "offsite_last_attempt": _iso(now)})
        return {"ok": True, "path": dst, "file": b["name"]}

    def _offsite_failed(self, now, text) -> dict:
        self._set({"offsite_last_error": text[:500],
                   "offsite_last_attempt": _iso(now)})
        logger.warning("custody: off-host copy failed: %s", text)
        return {"ok": False, "error": text}

    # ── the drill ──
    def drill_now(self, port: Optional[int] = None) -> dict:
        port = int(port or self.drill_port)
        now = self.clock()
        self._tried["drill"] = now
        result: Dict[str, Any] = {"ok": False, "at": _iso(now), "port": port,
                                  "backup": None, "checks": [], "problems": [],
                                  "rto_s": None}
        if not self._drill_lock.acquire(blocking=False):
            return dict(result, problems=["a drill is already running"])
        t0 = time.monotonic()
        scratch = None
        try:
            try:
                backups = [b for b in list_backups(self.backup_dir)
                           if b["kind"] != "pre_migration"]
            except OSError as exc:
                result["problems"].append("the backup folder could not be "
                                          "read: %s" % exc)
                return self.record_drill(result)
            if not backups:
                result["problems"].append("there is no backup to restore: no "
                                          "backup has completed yet")
                return self.record_drill(result)
            b = backups[0]
            result["backup"] = b["name"]
            os.makedirs(self.drill_dir, exist_ok=True)
            scratch = tempfile.mkdtemp(prefix="drill-", dir=self.drill_dir)
            target = os.path.join(scratch, "lem.db")
            # 1. restore, which re-verifies the manifest first
            try:
                done = restore(b["path"], target)
            except RestoreRefused as exc:
                result["checks"].append({"name": "manifest", "ok": False,
                                         "detail": "; ".join(exc.problems)})
                result["problems"] += exc.problems
                return self.record_drill(result)
            man = done["manifest"]
            result["checks"].append({"name": "manifest", "ok": True,
                                     "detail": "re-verified: %d day(s) of log "
                                               "digest, %d bench epoch(s)" % (
                                                   len(man.get("log_digest") or {}),
                                                   len(man.get("epochs") or []))})
            # 2. boot on the scratch port, read-only
            if not _port_free(port):
                text = "port %d is in use, so the scratch server could not " \
                       "start" % port
                result["checks"].append({"name": "healthz", "ok": False,
                                         "detail": text})
                result["problems"].append(text)
                return self.record_drill(result)
            boot = boot_and_ask(target, port, scratch)
            h = boot.get("healthz") or {}
            st = h.get("store") or {}
            hz = {"name": "healthz", "ok": False, "read_only": st.get("read_only"),
                  "seconds": boot.get("seconds")}
            why = []
            if not boot["ok"]:
                why.append(boot["problem"])
            else:
                if h.get("status") != "ok":
                    why.append("/healthz status %r" % h.get("status"))
                if st.get("read_only") is not True:
                    why.append("the scratch server did not open the store "
                               "read-only")
                if os.path.realpath(str(st.get("path") or "")) != \
                        os.path.realpath(target):
                    why.append("the scratch server opened %s, not the "
                               "restored copy" % st.get("path"))
                if str(st.get("schema_version")) != str(man.get("schema_version")):
                    why.append("schema_version %s, the manifest says %s" % (
                        st.get("schema_version"), man.get("schema_version")))
                if st.get("guards_missing") or st.get("foreign_triggers"):
                    why.append("guards not intact: %s %s" % (
                        st.get("guards_missing"), st.get("foreign_triggers")))
            hz["ok"] = not why
            hz["detail"] = ("answered in %.1f s, store read-only, schema %s, "
                            "guards intact" % (boot["seconds"],
                                               st.get("schema_version"))
                            if not why else "; ".join(why))
            result["checks"].append(hz)
            result["problems"] += why
            result["rto_s"] = round(time.monotonic() - t0, 2)
            # 3. counts, and the file unchanged by the boot
            try:
                con = sqlite3.connect(_ro_uri(target), uri=True)
                try:
                    counts = describe(con)["tables"]
                finally:
                    con.close()
                diffs = [t for t in set(counts) | set(man["tables"])
                         if counts.get(t) != man["tables"].get(t)]
                same_file = file_sha256(target) == man["sha256"]
                ok = not diffs and same_file
                key = ("lem_machine_log", "bench_record", "log_annotation",
                       "bench_cursor", "lem_machine_config")
                detail = ", ".join("%s %d" % (t, counts.get(t, 0))
                                   for t in key if t in counts)
                if diffs:
                    detail = "differ: " + ", ".join(sorted(diffs))
                if not same_file:
                    detail += "; the restored file changed while the scratch " \
                              "server had it open"
                result["checks"].append({"name": "counts", "ok": ok,
                                         "detail": detail})
                if not ok:
                    result["problems"].append("counts: " + detail)
            except (OSError, sqlite3.Error) as exc:
                result["checks"].append({"name": "counts", "ok": False,
                                         "detail": str(exc)})
                result["problems"].append("counts: %s" % exc)
            result["ok"] = not result["problems"]
            return self.record_drill(result)
        finally:
            self._drill_lock.release()
            if scratch:
                shutil.rmtree(scratch, ignore_errors=True)

    def record_drill(self, result: dict) -> dict:
        now = self.clock()
        at = result.get("at") or _iso(now)
        self._set({"drill_at": at, "drill_ok": "1" if result.get("ok") else "0",
                   "drill_backup": result.get("backup"),
                   "drill_rto_s": result.get("rto_s"),
                   "drill_detail": json.dumps(result, default=str)[:20000]})
        return result

    # ── the schedule ──
    @property
    def drill_running(self) -> bool:
        return self._drill_lock.locked()

    def _due(self, what: str, now: datetime) -> bool:
        st = self.state
        tried = self._tried.get(what)
        if what == "backup":
            last = _parse(st.get("last_backup_at"))
            if self._flag("last_backup_ok") is False:
                return tried is None or now - tried >= BACKUP_RETRY
            return last is None or now - last >= BACKUP_EVERY
        if what == "offsite":
            if not self.offsite_dir:
                return False
            if tried is not None and now - tried < OFFSITE_RETRY:
                return False
            last = _parse(st.get("offsite_last_ok"))
            if last is None:
                return True
            age = now - last
            local_hour = now.astimezone().hour
            return age >= OFFSITE_DAY or (age >= OFFSITE_NIGHT_AFTER and
                                          local_hour in OFFSITE_NIGHT_HOURS)
        if what == "drill":
            if not self.schedule_drill:
                return False
            if tried is not None and now - tried < DRILL_RETRY:
                return False
            last = _parse(st.get("drill_at"))
            if last is not None and self._flag("drill_ok") is False:
                return now - last >= DRILL_RETRY
            return last is None or now - last >= DRILL_EVERY
        return False

    def tick(self) -> List[str]:
        """Do whatever is due, in order: backup, off-host, drill. Returns the
        names of what ran."""
        if self.read_only:
            return []
        ran = []
        now = self.clock()
        if self._due("backup", now):
            self.backup_now()
            ran.append("backup")
        has_backup = self._flag("last_backup_ok") is True
        if has_backup and self._due("offsite", now):
            self.offsite_now()
            ran.append("offsite")
        if has_backup and self._due("drill", now):
            self.drill_now()
            ran.append("drill")
        return ran

    def start(self, every_s: float = 60.0) -> None:
        if self.read_only or self._thread is not None:
            return
        self.active = True

        def loop():
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception:                      # noqa: BLE001
                    logger.exception("custody: tick failed")
                self._stop.wait(every_s)
        self._thread = threading.Thread(target=loop, daemon=True,
                                        name="lem-custody")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ── what people see ──
    def _reconcile(self) -> dict:
        raw = self.state.get("reconcile")
        if not raw:
            return {"epochs": None, "titles": {}}
        try:
            v = json.loads(raw)
            return {"epochs": v.get("epochs"), "titles": v.get("titles") or {}}
        except (ValueError, AttributeError):
            return {"epochs": None, "titles": {}, "error": "unreadable"}

    def bridge_off_refusals(self) -> List[str]:
        """Why the bridge may not be turned off now (§11: the custody shift).
        Empty means custody allows it."""
        now = self.clock()
        if self.state.get("read_error"):
            return ["The backup state could not be read (%s), so whether an "
                    "off-host copy exists is unknown." % self.state["read_error"]]
        raw = self.state.get("offsite_last_ok")
        if not raw:
            return ["No off-host copy of the LEM store has ever completed. "
                    "Until one has, the record is less protected in the store "
                    "than inside LabCore's backup."]
        at = _parse(raw)
        if at is None:
            return ["The time of the last off-host copy (%r) cannot be read, "
                    "so its age is unknown." % raw]
        age = now - at
        if age > OFFSITE_MAX:
            return ["The last off-host copy is %d h old; the bridge can be "
                    "turned off only within 26 h of one." % _hours(age)]
        return []

    def status_items(self) -> List[dict]:
        """Global-status lines, most urgent first: {key, level, message,
        href, link}. Nothing before the first backup unless the scheduler is
        running (a test app or a dev page has nothing to be late about)."""
        now = self.clock()
        st = self.state
        out: List[dict] = []

        def item(key, level, message):
            out.append({"key": "custody:" + key, "level": level,
                        "message": message, "href": HREF, "link": LINK})

        if st.get("read_error"):
            item("state", "error", "The backup state of the LEM store could "
                                   "not be read: %s." % st["read_error"][:160])
            return out
        ok = self._flag("last_backup_ok")
        if ok is None and not self.active:
            return out
        last = _parse(st.get("last_backup_at"))
        if ok is False:
            item("backup:failed", "error",
                 "The last backup of the LEM store failed: %s."
                 % (st.get("last_backup_error") or "no reason recorded")[:200])
        elif ok is None:
            item("backup:none", "warning",
                 "The LEM store has not been backed up yet.")
        elif last is not None and now - last >= BACKUP_AMBER:
            item("backup:old", "warning",
                 "The newest backup of the LEM store is %d h old."
                 % _hours(now - last))
        rec = self._reconcile()
        for e in rec.get("epochs") or []:
            if e.get("ok"):
                continue
            title = rec["titles"].get(e["machine_uid"]) or e["machine_uid"]
            item("reconcile:%s:%s" % (e["machine_uid"], e["epoch"]), "error",
                 "%s: its journal and LEM disagree — %s."
                 % (title, (e.get("problems") or ["unknown"])[0]))
        off = _parse(st.get("offsite_last_ok"))
        if off is None:
            item("offsite:none", "warning",
                 "The LEM store has no off-host copy yet%s." % (
                     "" if self.offsite_dir else ": no target is named"))
        elif now - off > OFFSITE_MAX:
            item("offsite:old", "warning",
                 "The off-host copy of the LEM store is %d h old."
                 % _hours(now - off))
        d_at = _parse(st.get("drill_at"))
        d_ok = self._flag("drill_ok")
        if d_ok is False:
            detail = {}
            try:
                detail = json.loads(st.get("drill_detail") or "{}")
            except ValueError:
                pass
            first = (detail.get("problems") or ["no reason recorded"])[0]
            item("drill:failed", "error",
                 "The last restore drill failed: %s." % str(first)[:200])
        elif d_at is None:
            item("drill:none", "warning",
                 "No restore drill has been run on the LEM store yet.")
        elif now - d_at > DRILL_AMBER:
            item("drill:old", "warning",
                 "The last restore drill was %d days ago."
                 % (now - d_at).days)
        order = {"error": 0, "warning": 1, "info": 2}
        out.sort(key=lambda i: order.get(i["level"], 3))
        return out

    def health(self) -> dict:
        """The /healthz `store` keys (§12), from memory."""
        st = self.state
        return {"last_backup_at": st.get("last_backup_at"),
                "last_backup_ok": self._flag("last_backup_ok"),
                "offsite_last_ok": st.get("offsite_last_ok"),
                "drill_at": st.get("drill_at"),
                "drill_ok": self._flag("drill_ok")}

    def view(self) -> dict:
        """Settings › Backups: one row per fact, each a sentence, with a
        glyph, and a different sentence for "could not tell"."""
        now = self.clock()
        st = self.state
        rows = []
        try:
            backups = list_backups(self.backup_dir)
            listed = None
        except OSError as exc:
            backups, listed = [], str(exc)
        regular = [b for b in backups if b["kind"] != "pre_migration"]
        ok = self._flag("last_backup_ok")
        last = _parse(st.get("last_backup_at"))
        keep = ("Keeps the 48 newest, then one a day for 35 days and one a "
                "month for 24 months, in %s" % self.backup_dir)
        if listed:
            keep = "The backup folder could not be read: %s" % listed[:120]
        elif regular:
            keep = "%d %s held. " % (len(regular), "copy" if len(regular) == 1
                                     else "copies") + keep
        if st.get("read_error"):
            rows.append(_row("backup", "Last backup", "error",
                             "Could not be read", st["read_error"][:160]))
        elif ok is None:
            rows.append(_row("backup", "Last backup", "never", "No backup yet",
                             "The first is taken within the hour of the server "
                             "starting. " + keep))
        elif ok is False:
            rows.append(_row("backup", "Last backup", "error",
                             "Failed " + _when(last, now),
                             (st.get("last_backup_error") or "")[:200]))
        else:
            old = last is not None and now - last >= BACKUP_AMBER
            rows.append(_row("backup", "Last backup", "held" if old else "final",
                             "Checked " + _when(last, now),
                             "The copy passed integrity_check and has a manifest. "
                             + keep))
        off = _parse(st.get("offsite_last_ok"))
        target = self.offsite_dir or "no target is named (LEM_BACKUP_OFFSITE)"
        err = st.get("offsite_last_error")
        if off is None:
            rows.append(_row("offsite", "Off-host copy", "error" if err else "never",
                             "No off-host copy yet",
                             ("Last try: %s. " % err[:160] if err else "")
                             + "Target: " + target))
        else:
            stale = now - off > OFFSITE_MAX
            rows.append(_row("offsite", "Off-host copy",
                             "held" if stale or err else "final",
                             "Copied " + _when(off, now),
                             ("%s, its SHA-256 re-read at the target. " % (
                                 st.get("offsite_last_file") or "The newest backup"))
                             + ("Last try failed: %s. " % err[:120] if err else "")
                             + "Target: " + target))
        d_at = _parse(st.get("drill_at"))
        d_ok = self._flag("drill_ok")
        try:
            detail = json.loads(st.get("drill_detail") or "{}")
        except ValueError:
            detail = {}
        if self.drill_running:
            rows.append(_row("drill", "Restore drill", "working", "Running now",
                             "restoring the newest backup and booting it on "
                             "port %d" % self.drill_port))
        elif d_at is None:
            rows.append(_row("drill", "Restore drill", "never", "Never run",
                             "Monthly: the newest backup is restored to a "
                             "scratch folder, re-verified, booted read-only on "
                             "port %d and its counts compared." % self.drill_port))
        elif d_ok:
            rows.append(_row("drill", "Restore drill",
                             "held" if now - d_at > DRILL_AMBER else "final",
                             "Passed " + _when(d_at, now),
                             "%s restored and booted on port %s, ready in %s s; %s" % (
                                 detail.get("backup") or st.get("drill_backup") or "",
                                 detail.get("port"), st.get("drill_rto_s"),
                                 "; ".join(c.get("detail", "") for c in
                                           detail.get("checks") or []
                                           if c.get("name") == "counts")[:200])))
        else:
            rows.append(_row("drill", "Restore drill", "error",
                             "Failed " + _when(d_at, now),
                             "; ".join(detail.get("problems") or [])[:300]))
        rec = self._reconcile()
        eps = rec.get("epochs")
        if eps is None:
            rows.append(_row("reconcile", "Bench records", "never",
                             "Not compared yet",
                             "compared with every backup: LEM's records of each "
                             "bench epoch against its own digest and the bench's "
                             "count"))
        else:
            bad = [e for e in eps if not e.get("ok")]
            if bad:
                rows.append(_row("reconcile", "Bench records", "error",
                                 "%d of %d bench epoch%s disagree" % (
                                     len(bad), len(eps), "" if len(eps) == 1 else "s"),
                                 "; ".join("%s: %s" % (
                                     rec["titles"].get(e["machine_uid"]) or
                                     e["machine_uid"], e["problems"][0])
                                     for e in bad)[:300]))
            else:
                rows.append(_row("reconcile", "Bench records",
                                 "final" if eps else "never",
                                 "Every bench agrees with LEM" if eps
                                 else "No bench has synced yet",
                                 "%d epoch%s compared at the last backup" % (
                                     len(eps), "" if len(eps) == 1 else "s")))
        refusals = self.bridge_off_refusals()
        on = (st.get("bridge") or "on") != "off"
        rows.append(_row("bridge", "Bridge to LabCore",
                         "final" if on else "never",
                         "On" if on else "Off since " + _when(
                             _parse(st.get("bridge_changed_at")), now),
                         ("Cannot be turned off yet: " + " ".join(refusals))
                         if on and refusals else
                         ("Custody allows turning it off." if on else
                          "Turned off by %s." % (st.get("bridge_changed_by") or "?"))))
        return {"rows": rows, "bridge_on": on, "refusals": refusals,
                "drill_running": self.drill_running,
                "backups": {"count": len(regular), "dir": self.backup_dir,
                            "newest": regular[0]["name"] if regular else None,
                            "error": listed}}

    def set_bridge(self, on: bool, who: str) -> dict:
        now = self.clock()
        if not on:
            refusals = self.bridge_off_refusals()
            if refusals:
                return {"ok": False, "refusals": refusals}
        try:
            hist = json.loads(self.state.get("bridge_history") or "[]")
        except ValueError:
            hist = []
        hist = (hist + [{"to": "on" if on else "off", "by": who,
                         "at": _iso(now)}])[-50:]
        err = self._set({"bridge": "on" if on else "off",
                         "bridge_changed_at": _iso(now),
                         "bridge_changed_by": who,
                         "bridge_history": json.dumps(hist)})
        if err:
            return {"ok": False, "error": err}
        logger.warning("custody: bridge turned %s by %s", "on" if on else "off",
                       who)
        return {"ok": True}


def _row(key, label, glyph, value, note) -> dict:
    return {"key": key, "label": label, "glyph": glyph, "value": value,
            "note": note}


def _when(at: Optional[datetime], now: datetime) -> str:
    if at is None:
        return "at an unknown time"
    local, today = at.astimezone(), now.astimezone()
    if local.date() == today.date():
        return local.strftime("%H:%M") + " today"
    if local.year == today.year:
        return "%d %s %s" % (local.day, local.strftime("%b"),
                             local.strftime("%H:%M"))
    return "%d %s %d" % (local.day, local.strftime("%b"), local.year)


# ── the routes ───────────────────────────────────────────────────────────────

def attach(app, cust: Custody) -> None:
    """Make `cust` this app's custody service, and add its routes once.
    Routes read the service from `app.config` at request time, so a test (or
    the server's boot) can swap it."""
    app.config["CUSTODY"] = cust
    if app.config.get("_CUSTODY_ROUTES"):
        return
    app.config["_CUSTODY_ROUTES"] = True
    from flask import jsonify, request, session

    def _c() -> Custody:
        return app.config["CUSTODY"]

    def _authed() -> bool:
        return bool(session.get("user"))

    def _deny():
        return jsonify({"error": "Authentication required"}), 401

    @app.route("/api/custody")
    def custody_state():
        """Settings › Backups: memory plus one listing of the backup folder.
        Asks LabCore nothing."""
        c = _c()
        view = c.view()
        resp = jsonify({"view": view, "backups": view["backups"],
                        "health": c.health(), "items": c.status_items()})
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/api/custody/backup", methods=["POST"])
    def custody_backup():
        if not _authed():
            return _deny()
        out = _c().backup_now()
        body = {k: out.get(k) for k in ("ok", "error", "path", "reconcile")}
        body["view"] = _c().view()
        return jsonify(body), (200 if out.get("ok") else 503)

    @app.route("/api/custody/offsite", methods=["POST"])
    def custody_offsite():
        if not _authed():
            return _deny()
        out = _c().offsite_now()
        return jsonify(dict(out, view=_c().view())), (200 if out.get("ok") else 503)

    @app.route("/api/custody/drill", methods=["POST"])
    def custody_drill():
        """Starts the drill and answers at once: it boots a second server and
        takes seconds. Progress is in Running now; the result in /api/custody."""
        if not _authed():
            return _deny()
        c = _c()
        if c.read_only:
            return jsonify({"error": "the store is open read-only"}), 409
        if c.drill_running:
            return jsonify({"error": "a drill is already running"}), 409
        if c.read_only is False and not list_backups_safe(c.backup_dir):
            return jsonify({"error": "there is no backup to restore yet: "
                                     "take one first"}), 409
        jobs = app.config.get("JOBS")
        job = jobs.start("drill", "Restore drill", by=session.get("user"),
                         open_url=HREF) if jobs is not None else None

        def run():
            try:
                out = c.drill_now()
            except Exception as exc:                   # noqa: BLE001
                logger.exception("custody: drill crashed")
                out = c.record_drill({"ok": False, "problems": [str(exc)]})
            if job is not None:
                if out.get("ok"):
                    job.finish("Passed: restored %s and booted it read-only "
                               "in %s s" % (out.get("backup"), out.get("rto_s")))
                else:
                    job.fail("Failed: %s" % (out.get("problems") or ["?"])[0])
        threading.Thread(target=run, daemon=True, name="lem-drill").start()
        return jsonify({"started": True}), 202

    @app.route("/api/transfer/bridge", methods=["GET", "POST"])
    def transfer_bridge():
        """The bridge switch (§10.4, §11). Turning it OFF is refused while
        custody cannot vouch for the store: no off-host copy in 26 h."""
        c = _c()
        if request.method == "GET":
            v = c.view()
            return jsonify({"on": v["bridge_on"], "refusals": v["refusals"]})
        if not _authed():
            return _deny()
        body = request.get_json(silent=True) or {}
        if not isinstance(body.get("on"), bool):
            return jsonify({"error": "say {\"on\": true} or {\"on\": false}"}), 400
        out = c.set_bridge(body["on"], session.get("user") or "someone")
        if out.get("refusals"):
            return jsonify({"error": "refused", "refusals": out["refusals"]}), 409
        if not out.get("ok"):
            return jsonify({"error": "LEM's store could not record the change: "
                                     "%s; nothing changed" % out.get("error")}), 503
        return jsonify({"ok": True, "on": body["on"]})


# ── command line ─────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("backup", "list"):
        s = sub.add_parser(name)
        s.add_argument("--store", default=None)
        s.add_argument("--backup-dir", default=None)
    s = sub.add_parser("verify")
    s.add_argument("backup")
    s = sub.add_parser("restore")
    s.add_argument("backup")
    s.add_argument("--store", default=None)
    args = p.parse_args(argv)
    from lem_store import default_store_path
    if args.cmd == "verify":
        v = verify(args.backup)
        print("OK" if v["ok"] else "FAILED")
        for line in v["problems"]:
            print(" -", line)
        return 0 if v["ok"] else 1
    if args.cmd == "restore":
        store = args.store or default_store_path()
        try:
            out = restore(args.backup, store)
        except RestoreRefused as exc:
            print("REFUSED: the backup does not re-verify:")
            for line in exc.problems:
                print(" -", line)
            return 1
        print("Restored %s over %s; the file it replaced is kept at %s"
              % (args.backup, store, out["kept"]))
        return 0
    store = args.store or default_store_path()
    bdir = args.backup_dir or default_backup_dir(store)
    if args.cmd == "list":
        for b in list_backups(bdir):
            print(b["at"].isoformat(), b["kind"], b["path"])
        return 0
    out = take_backup(store, bdir, _utcnow())
    print("Backed up to", out["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
