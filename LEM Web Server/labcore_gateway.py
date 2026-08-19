#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
labcore_gateway.py — LEM's access seam to LabCore.

LEM never opens a raw DB connection. It talks to LabCore's HTTP write-queue
gateway exactly like the other LabLink apps, through the vendored
``labcore_client.LabCoreClient``. This module wraps that client behind a small
interface so tests (and offline dev) can substitute an in-memory fake.

Two implementations:

- ``HttpLabCoreGateway``  — production: delegates to ``LabCoreClient`` (HTTP).
- ``FakeLabCoreGateway``  — tests/offline: an in-memory SQLite that mirrors
  LabCore's operations and response shapes.

Both expose the same method surface used by the rest of LEM:
    is_running() -> bool
    write(operation, params) -> dict          # generic queue write
    sql(sql, args=None) -> dict               # raw_sql shorthand
    read_sql(sql, args=None) -> dict           # {ok, rows, columns}
    get_samples(**params) -> dict | None
    get_test_names() -> list | None
"""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional

# The connection point shared across the LabLink suite: LabCore's HTTP queue is
# reverse-proxied behind this hostname over HTTPS (port 443, no :8080). Override
# per-machine with the LABCORE_URL env var, exactly like the other apps.
DEFAULT_LABCORE_URL = "https://labvision.asaplabs.net"


# LabCore's real core tables (from apps/LabCore/src/LabCore.py). The fake
# preseeds these so LEM's data source can query QC data offline.
_CORE_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS samples (
        lab_id TEXT PRIMARY KEY, sample_date_key TEXT, order_id TEXT,
        sample_id TEXT, customer_name TEXT, tracking_number TEXT,
        rush_status TEXT, testing_package TEXT, fuel_type TEXT,
        po_number TEXT, work_order TEXT, tank_number TEXT,
        sample_from TEXT, collection_date TEXT, tank_capacity TEXT,
        site_location TEXT, notes TEXT, prep_tests TEXT,
        raw_json TEXT, first_seen_at TEXT, last_seen_at TEXT,
        Photos TEXT, PhotoNames TEXT, PhotosBlob BLOB, sample_received TEXT)
    """,
    """
    CREATE TABLE IF NOT EXISTS sample_tests (
        lab_id TEXT NOT NULL, test_name TEXT NOT NULL, result TEXT,
        updated_at TEXT, PRIMARY KEY (lab_id, test_name))
    """,
    """
    CREATE TABLE IF NOT EXISTS sample_test_results (
        lab_id TEXT NOT NULL, test_name TEXT NOT NULL,
        result_value TEXT, updated_at TEXT NOT NULL,
        source_workspace TEXT, PRIMARY KEY (lab_id, test_name))
    """,
)


class FakeLabCoreGateway:
    """In-memory stand-in for LabCore, backed by SQLite.

    Mirrors the subset of LabCore operations LEM uses and returns the same
    response dict shapes as ``LabCoreClient`` so production and test paths are
    interchangeable.
    """

    def __init__(self, source: str = "LabEquipmentManager") -> None:
        self._source = source
        # check_same_thread=False + a lock mirrors LabCore's real behaviour: a
        # single serialized writer/reader that any thread (e.g. Flask's threaded
        # dev server) can call. The lock keeps concurrent access safe.
        self._lock = threading.RLock()
        self._con = sqlite3.connect(":memory:", check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA foreign_keys=ON")
        for stmt in _CORE_SCHEMA:
            self._con.execute(stmt)
        self._con.commit()

    # ── connectivity ─────────────────────────────────────────────────
    def is_running(self) -> bool:
        return True

    # ── generic write / raw sql ──────────────────────────────────────
    def sql(self, sql: str, args: Optional[list] = None, **_kw) -> dict:
        try:
            with self._lock:
                cur = self._con.execute(sql, args or [])
                self._con.commit()
                return {"ok": True, "rows_affected": cur.rowcount if cur.rowcount != -1 else 0}
        except sqlite3.Error as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    def read_sql(self, sql: str, args: Optional[list] = None, **_kw) -> dict:
        try:
            with self._lock:
                cur = self._con.execute(sql, args or [])
                rows = cur.fetchall()
                columns = [d[0] for d in cur.description] if cur.description else []
                return {
                    "ok": True,
                    "rows": [{k: r[k] for k in columns} for r in rows],
                    "columns": columns,
                }
        except sqlite3.Error as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    def write(self, operation: str, params: dict, **_kw) -> dict:
        handler = getattr(self, f"_op_{operation}", None)
        if handler is None:
            return {"error": f"Unknown operation: {operation}"}
        return handler(params or {})

    # ── named operations (subset used by LEM/tests) ──────────────────
    def _op_raw_sql(self, p: dict) -> dict:
        return self.sql(p.get("sql", ""), p.get("args") or [])

    def _op_read_sql(self, p: dict) -> dict:
        return self.read_sql(p.get("sql", ""), p.get("args") or [])

    def _op_insert_sample(self, p: dict) -> dict:
        lab_id = p.get("lab_id")
        if not lab_id:
            return {"error": "lab_id is required"}
        cols = {"lab_id": lab_id}
        if p.get("sample_id") is not None:
            cols["sample_id"] = p["sample_id"]
        if p.get("customer") is not None:
            cols["customer_name"] = p["customer"]
        if p.get("received_at") is not None:
            cols["sample_received"] = p["received_at"]
        for k, v in (p.get("extra_fields") or {}).items():
            cols[k] = v
        placeholders = ", ".join(["?"] * len(cols))
        colnames = ", ".join(cols.keys())
        sql = f"INSERT OR REPLACE INTO samples ({colnames}) VALUES ({placeholders})"
        return self.sql(sql, list(cols.values()))

    def _op_add_test(self, p: dict) -> dict:
        return self.sql(
            "INSERT OR IGNORE INTO sample_tests (lab_id, test_name, result) VALUES (?, ?, NULL)",
            [p.get("lab_id"), p.get("test_name")],
        )

    def _op_update_cell(self, p: dict) -> dict:
        return self.sql(
            "INSERT INTO sample_tests (lab_id, test_name, result, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(lab_id, test_name) DO UPDATE SET result=excluded.result, "
            "updated_at=excluded.updated_at",
            [p.get("lab_id"), p.get("test_name"), p.get("value"), p.get("updated_at")],
        )

    # ── data read helpers (GET-style) ────────────────────────────────
    def get_samples(self, **params) -> Optional[dict]:
        res = self.read_sql("SELECT * FROM samples")
        return {"samples": res.get("rows", [])} if res.get("ok") else None

    def get_test_names(self, **_kw) -> Optional[list]:
        res = self.read_sql("SELECT DISTINCT test_name FROM sample_tests ORDER BY test_name")
        if not res.get("ok"):
            return None
        return [r["test_name"] for r in res["rows"]]


def resolve_labcore_url(base_url: Optional[str] = None) -> str:
    """Resolve the LabCore base URL the same way the rest of the suite does.

    Order: explicit arg → LABCORE_URL env → DEFAULT_LABCORE_URL. Trailing
    slashes are trimmed so ``{base}/api/...`` joins cleanly.
    """
    url = base_url or os.environ.get("LABCORE_URL") or DEFAULT_LABCORE_URL
    return url.rstrip("/")


def _client_const(name: str, fallback):
    """A timeout constant from the vendored client, or a sane fallback.

    Read rather than copied: if LabLink retunes its timeouts on a re-sync, the
    overrides below follow instead of quietly keeping the old numbers.
    """
    try:
        import labcore_client
        return getattr(labcore_client, name, fallback)
    except Exception:
        return fallback


def _build_session():
    """A pooled `requests.Session` sized for the snapshot's fan-out.

    The default pool holds 10 connections and *discards* the excess — which
    rebuilds them, and their TLS state, exactly when the fallback path fans out
    across threads. Sized to the worker cap with headroom for Flask's own threads.
    """
    import requests
    from requests.adapters import HTTPAdapter

    try:
        from snapshot_service import MAX_WORKERS
    except Exception:
        MAX_WORKERS = 8
    size = max(16, MAX_WORKERS * 2)
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=size, pool_maxsize=size)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class _UrlClient:
    """Vendored ``LabCoreClient`` re-pointed at a full base URL.

    The stock client builds ``http://host:port``; the suite's real connection
    point is ``https://labvision.asaplabs.net`` (no port, TLS). This subclass
    overrides only ``base_url`` so all of the client's request/retry/timeout
    logic is reused unchanged.
    """

    def __new__(cls, base_url: str, source: str):
        from labcore_client import LabCoreClient  # lazy: needs requests

        class _Client(LabCoreClient):
            @property
            def base_url(self_inner) -> str:
                return base_url

            def get_test_names_raw(self_inner, timeout: float = 45):
                """The method list, un-interpreted.

                The vendored ``get_test_names`` reads ``data["test_names"]``,
                but LabCore answers ``{"tests": [...]}`` — so it always
                returned []. That bug lives in LabLink's own client (LabStation
                and LabEntry share it; LabOut-Server does not), and we don't
                edit the vendored file. Fetch the payload here and let the
                gateway decide what shape it is.

                The default timeout is generous on purpose: LabCore may run a
                DISTINCT scan over hundreds of thousands of rows, and the
                client's 8s read timeout loses that race.
                """
                resp = self_inner.session.get(f"{base_url}/api/test-names",
                                              timeout=timeout)
                resp.raise_for_status()
                return resp.json()

            # ── one pooled session for the whole process ──────────────
            # Reported from the lab: the server was caught at 950% CPU — 9.5 of
            # 12 cores. The vendored client calls module-level `requests.get` /
            # `requests.post`, and each of those builds a fresh Session →
            # HTTPAdapter → PoolManager → SSLContext, parsing certifi's 228 KB
            # cacert.pem from scratch. On Windows that measured **0.441s of CPU
            # per call against 0.009s with a reused session — 47x**. Worse, it is
            # genuinely parallel: OpenSSL drops the GIL, so concurrent reads
            # become concurrent cores rather than queueing behind one.
            #
            # Cutting the NUMBER of reads (snapshot_service.py) shrank the spike;
            # this removes its cause. Every method below is overridden purely to
            # route through `self.session` — the request/retry/timeout logic is
            # still the vendored client's.
            def __init__(self_inner, *a, **kw):
                super().__init__(*a, **kw)
                self_inner._session = None
                self_inner._session_lock = threading.Lock()

            @property
            def session(self_inner):
                """Created once, shared by every thread.

                `requests.Session` is safe to *use* concurrently — urllib3's
                PoolManager is thread-safe and nothing here mutates the session
                after construction. A session per thread would rebuild the
                SSLContext per thread, which is the same bug in miniature.
                """
                if self_inner._session is None:
                    with self_inner._session_lock:
                        if self_inner._session is None:
                            self_inner._session = _build_session()
                return self_inner._session

            @session.setter
            def session(self_inner, value):
                self_inner._session = value

            # ── the overrides, all one-liners onto the session ────────
            def write(self_inner, operation, params, source=None, timeout=None):
                import requests

                src = source or self_inner._source
                try:
                    resp = self_inner.session.post(
                        f"{self_inner.base_url}/api/queue/write",
                        json={"operation": operation, "params": params,
                              "source": src},
                        timeout=timeout or _client_const("DEFAULT_TIMEOUT", 30))
                    return resp.json()
                except requests.ConnectionError:
                    return {"error": "LabCore is not running. "
                                     "Start LabCore first."}
                except Exception as exc:
                    return {"error": str(exc)}

            def is_running(self_inner) -> bool:
                try:
                    resp = self_inner.session.get(
                        f"{self_inner.base_url}/api/queue/status",
                        timeout=_client_const("STATUS_TIMEOUT", 5))
                    return resp.status_code == 200
                except Exception:
                    return False

            def get_queue_status(self_inner):
                try:
                    return self_inner.session.get(
                        f"{self_inner.base_url}/api/queue/status",
                        timeout=_client_const("STATUS_TIMEOUT", 5)).json()
                except Exception:
                    return None

            def test_connection(self_inner):
                try:
                    resp = self_inner.session.get(
                        f"{self_inner.base_url}/api/queue/status",
                        timeout=_client_const("STATUS_TIMEOUT", 5))
                    if resp.status_code == 200:
                        total = resp.json().get("total_processed", "?")
                        return True, (f"Connected to LabCore — {total} "
                                      f"operations processed.")
                    return False, (f"LabCore responded with status "
                                   f"{resp.status_code}.")
                except Exception as exc:
                    return False, str(exc)

            def get_samples(self_inner, **params):
                try:
                    resp = self_inner.session.get(
                        f"{self_inner.base_url}/api/samples", params=params,
                        timeout=_client_const("READ_TIMEOUT", 8))
                    return resp.json()
                except Exception as exc:
                    logger.warning("Failed to read samples from LabCore: %s", exc)
                    return None

        return _Client(source=source)


class HttpLabCoreGateway:
    """Production gateway: delegates to the vendored ``LabCoreClient`` (HTTP),
    pointed at the suite's shared connection point (``labvision.asaplabs.net``).
    """

    def __init__(self, base_url: Optional[str] = None,
                 source: str = "LabEquipmentManager") -> None:
        self.base_url = resolve_labcore_url(base_url)
        self._client = _UrlClient(self.base_url, source)

    def is_running(self) -> bool:
        return self._client.is_running()

    def write(self, operation: str, params: dict, **kw) -> dict:
        return self._client.write(operation, params, **kw)

    def sql(self, sql: str, args: Optional[list] = None, **kw) -> dict:
        return self._client.sql(sql, args, **kw)

    def read_sql(self, sql: str, args: Optional[list] = None, **kw) -> dict:
        kw.setdefault("timeout", READ_TIMEOUT)
        return self._client.read_sql(sql, args, **kw)

    def get_samples(self, **params) -> Optional[dict]:
        return self._client.get_samples(**params)

    # LabCore has answered this three different ways across versions. Accept
    # all of them rather than swap one hard-coded key for another.
    TEST_NAME_KEYS = ("tests", "test_names")
    TEST_NAMES_TIMEOUT = 45

    def get_test_names(self, **kw) -> Optional[list]:
        """Every test method LabCore knows about.

        Returns None when LabCore could not be asked, and [] when it answered
        with nothing — the caller falls back only on the former.
        """
        timeout = kw.get("timeout") or self.TEST_NAMES_TIMEOUT
        try:
            payload = self._client.get_test_names_raw(timeout=timeout)
        except Exception:
            return None
        if isinstance(payload, dict):
            for key in self.TEST_NAME_KEYS:
                if isinstance(payload.get(key), list):
                    payload = payload[key]
                    break
            else:
                return None
        if not isinstance(payload, list):
            return None
        return [str(n).strip() for n in payload
                if n is not None and str(n).strip()]


# Every read here POSTs to /api/queue/write and therefore waits behind every write
# in the lab. The vendored client allows 8s, which is not a measure of how slow the
# query is — measured live, the batched read takes 0.12s while the queue bursts to 81
# pending and throughput drops to 0.1 ops/sec. Raised here because this is the layer
# that knows that about LabCore; fixing it here covers every call site instead of
# leaving each one to guess. Request paths get this; the background poller asks for
# more (snapshot_service.READ_TIMEOUT) because nobody is waiting on it.
READ_TIMEOUT = 20.0


def existing_tables(gateway, timeout: float = 45.0):
    """Which tables LabCore already has, or None if we could not find out.

    `CREATE TABLE IF NOT EXISTS` is harmless but not free: it goes through the
    same serialised write queue as the rest of the lab, which lands about 1.5
    ops/sec. Fifteen of them on every start — ten for the snapshot's tables, five
    for the config store's — is ten seconds of queue for tables that already
    exist, and the tray restarts this server on every code edit.

    Returns None rather than an empty set when the question cannot be answered,
    because "nothing exists" is a real answer for a fresh database and callers must
    be able to tell it apart from "no idea, declare everything".

    Asks `pragma_table_list`, not `sqlite_master`. Against production the
    sqlite_master form **times out** — the client allows 8s and that query does not
    return inside it, though `SELECT COUNT(*) FROM sqlite_master` answers 110
    instantly. So this quietly returned None on every boot and the writes it exists
    to avoid were issued anyway. pragma_table_list answers at once, and needs SQLite
    3.37 (production runs 3.49); the sqlite_master form stays as a fallback for
    anything older.
    """
    for sql in ("SELECT name FROM pragma_table_list WHERE type = 'table'",
                "SELECT name FROM sqlite_master WHERE type = 'table'"):
        try:
            # Generous, for the same reason SnapshotService.READ_TIMEOUT is: reads
            # POST to /api/queue/write and wait behind every write in the lab.
            res = gateway.read_sql(sql, timeout=timeout)
        except Exception:
            return None
        if not res or res.get("error"):
            continue            # unsupported or slow: try the next form
        return {str(r.get("name")) for r in (res.get("rows") or [])
                if r.get("name")}
    return None
