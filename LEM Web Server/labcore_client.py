"""
LabCore Write Queue Client
--------------------------
HTTP client for sending write operations to LabCore's queue API.
Reads remain direct sqlite3 — only writes go through LabCore.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8080
# Per-call timeouts. Kept aggressive on purpose: every one of these calls
# can land on a GUI thread, so a 30-second stall = Windows marks LabStation
# "Not Responding". A flaky LabCore should produce a quick error instead of
# a multi-second freeze; reads + writes can be retried by the caller.
DEFAULT_TIMEOUT = 8     # writes (POST /api/queue/write)
READ_TIMEOUT = 5        # reads (GET /api/samples, /api/test-names)
STATUS_TIMEOUT = 2      # is_running() / test_connection() probes
# Photos are multi-MB base64 payloads; the aggressive write timeout above
# routinely times out mid-upload. Give them a generous ceiling that still
# sits just under LabCore's own 30s submit_and_wait limit, so the client
# gives up at roughly the same moment the server would.
PHOTO_TIMEOUT = 28

# Bounded retry for *idempotent* reads/probes only. A single transient
# ConnectionError/Timeout (LabCore mid-restart, a dropped keep-alive) should
# not surface as a hard failure to the caller. Writes are NOT retried here —
# they are not idempotent and the app-level write queue already retries them
# safely with (lab_id, test_name) dedup.
READ_RETRIES = 2        # total attempts for reads = 1 + (READ_RETRIES - 1)
READ_RETRY_BACKOFF = 0.4  # seconds, multiplied by attempt index

_TRANSIENT_EXC: tuple = ()
if requests is not None:
    _TRANSIENT_EXC = (requests.ConnectionError, requests.Timeout)


def _retry_read(fn: Callable[[], Any], attempts: int = READ_RETRIES):
    """Run *fn* (an idempotent GET/read) with bounded retry on transient
    network errors. Re-raises the last exception if every attempt fails so the
    caller's existing except-handling still runs."""
    last_exc: Optional[BaseException] = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except _TRANSIENT_EXC as exc:  # type: ignore[misc]
            last_exc = exc
            if i < attempts - 1:
                time.sleep(READ_RETRY_BACKOFF * (i + 1))
    if last_exc is not None:
        raise last_exc
    return fn()


class LabCoreClient:
    """Thin wrapper around LabCore's HTTP write-queue API."""

    def __init__(self, host: str = "localhost", port: int = DEFAULT_PORT, port_enabled: bool = True, source: str = "LabStation") -> None:
        self._host = host
        self._port = port
        self._port_enabled = port_enabled
        self._source = source

    # ── properties ────────────────────────────────────────────────────
    @property
    def host(self) -> str:
        return self._host

    @host.setter
    def host(self, value: str) -> None:
        self._host = value.strip() or "localhost"

    @property
    def port(self) -> int:
        return self._port

    @port.setter
    def port(self, value: int) -> None:
        self._port = int(value)

    @property
    def port_enabled(self) -> bool:
        return self._port_enabled

    @port_enabled.setter
    def port_enabled(self, value: bool) -> None:
        self._port_enabled = value

    @property
    def base_url(self) -> str:
        if self._port_enabled:
            return f"http://{self._host}:{self._port}"
        return f"http://{self._host}"

    # ── connectivity ──────────────────────────────────────────────────
    def is_running(self) -> bool:
        """Return True if LabCore is reachable."""
        if requests is None:
            return False
        try:
            resp = requests.get(f"{self.base_url}/api/queue/status", timeout=STATUS_TIMEOUT)
            return resp.status_code == 200
        except Exception:
            return False

    def test_connection(self) -> tuple[bool, str]:
        """Test the connection and return (ok, message)."""
        if requests is None:
            return False, "The 'requests' library is not installed."
        try:
            resp = requests.get(f"{self.base_url}/api/queue/status", timeout=STATUS_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                total = data.get("total_processed", "?")
                return True, f"Connected to LabCore — {total} operations processed."
            return False, f"LabCore responded with status {resp.status_code}."
        except requests.ConnectionError:
            return False, f"Cannot reach LabCore at {self.base_url}. Is it running?"
        except Exception as exc:
            return False, str(exc)

    # ── generic write ─────────────────────────────────────────────────
    def write(self, operation: str, params: dict, source: Optional[str] = None,
              timeout: Optional[float] = None) -> dict:
        """Send a write operation to LabCore's queue.  Blocks until complete.

        ``timeout`` overrides the default per-call timeout — used by callers
        running on a worker thread (e.g. the test-name bootstrap) where a
        longer wait is safe because it can't freeze the GUI.
        """
        if requests is None:
            return {"error": "The 'requests' library is not installed."}
        src = source or self._source
        try:
            resp = requests.post(
                f"{self.base_url}/api/queue/write",
                json={"operation": operation, "params": params, "source": src},
                timeout=timeout or DEFAULT_TIMEOUT,
            )
            return resp.json()
        except requests.ConnectionError:
            return {"error": "LabCore is not running. Start LabCore first."}
        except Exception as exc:
            return {"error": str(exc)}

    def sql(self, sql: str, args: Optional[list] = None, source: Optional[str] = None,
            timeout: Optional[float] = None) -> dict:
        """Execute arbitrary write SQL through LabCore.

        ``timeout`` overrides the default write timeout — used by callers that
        ship large payloads (e.g. base64 photos) where the aggressive default
        would time out mid-transfer.
        """
        return self.write("raw_sql", {"sql": sql, "args": args or []},
                          source=source, timeout=timeout)

    def read_sql(self, sql: str, args: Optional[list] = None, source: Optional[str] = None,
                 timeout: Optional[float] = None) -> dict:
        """Execute a SELECT query through LabCore and return rows."""
        return self.write("read_sql", {"sql": sql, "args": args or []},
                          source=source, timeout=timeout)

    # ── named operations ──────────────────────────────────────────────
    def insert_sample(
        self,
        lab_id: str,
        *,
        sample_id: Optional[str] = None,
        customer: Optional[str] = None,
        received_at: Optional[str] = None,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> dict:
        params: Dict[str, Any] = {"lab_id": lab_id}
        if sample_id is not None:
            params["sample_id"] = sample_id
        if customer is not None:
            params["customer"] = customer
        if received_at is not None:
            params["received_at"] = received_at
        if extra_fields:
            params["extra_fields"] = extra_fields
        return self.write("insert_sample", params)

    def update_cell(self, lab_id: str, test_name: str, value: str) -> dict:
        return self.write("update_cell", {"lab_id": lab_id, "test_name": test_name, "value": value})

    def update_sample_field(self, lab_id: str, field: str, value: str) -> dict:
        return self.write("update_sample_field", {"lab_id": lab_id, "field": field, "value": value})

    def add_test(self, lab_id: str, test_name: str) -> dict:
        return self.write("add_test", {"lab_id": lab_id, "test_name": test_name})

    def remove_test(self, lab_id: str, test_name: str) -> dict:
        return self.write("remove_test", {"lab_id": lab_id, "test_name": test_name})

    def add_column(self, column_name: str, table: str = "samples", col_type: str = "TEXT") -> dict:
        return self.write("add_column", {"column_name": column_name, "table": table, "col_type": col_type})

    def batch(self, operations: List[Dict[str, Any]]) -> dict:
        """Execute multiple operations in a single atomic transaction."""
        return self.write("batch", {"operations": operations})

    def append_photo(
        self,
        lab_id: str,
        data_uri: str,
        *,
        blob_hex: Optional[str] = None,
        filename: Optional[str] = None,
        replace: bool = False,
        timeout: Optional[float] = None,
    ) -> dict:
        """Atomically append one photo (a data-URI) to a sample's Photos list.

        The append happens inside LabCore's single-threaded write queue, so two
        photos landing on the same sample can never clobber each other the way a
        client-side read-modify-write does. ``filename`` is stored parallel to
        the image so the original name survives to the customer portal. Uses
        ``PHOTO_TIMEOUT`` by default because the base64 payload is large.

        With ``replace=True`` an existing photo of the SAME filename is
        overwritten in place instead of adding a duplicate.
        """
        params: Dict[str, Any] = {"lab_id": lab_id, "data_uri": data_uri}
        if blob_hex is not None:
            params["blob_hex"] = blob_hex
        if filename:
            params["filename"] = filename
        if replace:
            params["replace"] = True
        return self.write("append_photo", params, timeout=timeout or PHOTO_TIMEOUT)

    # ── high-level helpers ────────────────────────────────────────────
    def send_sample_with_tests(
        self,
        lab_id: str,
        *,
        sample_id: Optional[str] = None,
        customer: Optional[str] = None,
        extra_fields: Optional[Dict[str, Any]] = None,
        test_names: Optional[List[str]] = None,
    ) -> dict:
        """Insert a sample and assign tests in one atomic batch."""
        ops: List[Dict[str, Any]] = []
        insert_params: Dict[str, Any] = {"lab_id": lab_id}
        if sample_id is not None:
            insert_params["sample_id"] = sample_id
        if customer is not None:
            insert_params["customer"] = customer
        if extra_fields:
            insert_params["extra_fields"] = extra_fields
        ops.append({"operation": "insert_sample", "params": insert_params})
        for name in (test_names or []):
            if name and name.strip():
                ops.append({"operation": "add_test", "params": {"lab_id": lab_id, "test_name": name.strip()}})
        return self.batch(ops)

    # ── data read helpers (GET endpoints) ─────────────────────────────
    def get_samples(self, **params) -> Optional[dict]:
        """Read samples via LabCore GET endpoint (convenience)."""
        if requests is None:
            return None
        try:
            resp = _retry_read(lambda: requests.get(
                f"{self.base_url}/api/samples", params=params, timeout=READ_TIMEOUT))
            return resp.json()
        except Exception as exc:
            logger.warning("Failed to read samples from LabCore: %s", exc)
            return None

    def get_test_names(self, timeout: Optional[float] = None) -> Optional[list]:
        """Fetch all distinct test names from LabCore.

        ``/api/test-names`` runs a DISTINCT scan that can take several
        seconds on a busy LabCore. Callers on a worker thread should pass a
        generous ``timeout`` so a slow-but-alive server still returns the
        full list instead of the aggressive default timing out to empty.
        """
        if requests is None:
            return None
        try:
            resp = _retry_read(lambda: requests.get(
                f"{self.base_url}/api/test-names", timeout=timeout or READ_TIMEOUT))
            data = resp.json()
            return data if isinstance(data, list) else data.get("test_names", [])
        except Exception as exc:
            logger.warning("Failed to read test names from LabCore: %s", exc)
            return None

    def get_queue_status(self) -> Optional[dict]:
        if requests is None:
            return None
        try:
            resp = requests.get(f"{self.base_url}/api/queue/status", timeout=STATUS_TIMEOUT)
            return resp.json()
        except Exception:
            return None
