"""A LEM v4 server, faked at the wire, for the bench's transfer tests.

It speaks the v2 bench protocol the way `LEM Web Server/bench_api.py` does —
the same paths, the same status codes, the same body shapes — and nothing
more. The REAL server is run against the REAL module by the gate
(`gauntlet-harness`, scenarios E0–E3, T1–T4b, D1, CF1, CF2); this fake exists
so the module's own suite (which has no Flask) can pin the bench's half of
the contract and do it in milliseconds.

Reached through a patched `urllib.request.urlopen`, routed by host:

    192.168.1.5:5557   road "lan"
    lem.asaplabs.net   road "public"

Each road has a mode: up, down (connection refused), timeout, 404 (an old
server), 503 (store busy), slow (answers after `slow_seconds`).

Like Cloudflare in production, the public road refuses a request that does
not name itself — urllib's default "Python-urllib/x.y" — with 403 "error
code: 1010". The bench only works here if it sends its own User-Agent.

Everything it is asked is recorded with the THREAD that asked, which is how
the tests prove the poll never does LEM I/O.
"""
import base64
import email.message
import io
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

HOSTS = {"192.168.1.5:5557": "lan", "lem.asaplabs.net": "public"}


def _headers(pairs):
    m = email.message.Message()
    for k, v in pairs:
        m[k] = v
    return m


class _Resp(io.BytesIO):
    def __init__(self, status, body, headers=()):
        super().__init__(body)
        self.status = status
        self.code = status
        self.headers = _headers(list(headers))

    def getcode(self):
        return self.status

    def info(self):
        return self.headers

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _err(url, code, body=b"", headers=()):
    return urllib.error.HTTPError(url, code, "x", _headers(list(headers)),
                                  io.BytesIO(body))


def canonical(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")


class FakeLem:
    def __init__(self, uid="m1", shared="shared-token", known=True):
        self.uid = uid
        self.shared = shared
        self.known = known
        self.modes = {"lan": "up", "public": "up"}
        self.slow_seconds = 2.0
        self.lock = threading.Lock()
        self.requests = []          # dicts: road, method, path, thread, ua
        self.token = None
        self.enrolled_once = False
        self.approved = False
        self.enroll_key = None
        self.cursor = {}            # epoch -> acked
        self.records = []           # every NEW record stored (body dicts)
        self.resent = 0
        self.config_rev = "rev-1"
        self.corrections = []       # rows: machine_uid, test_name, correction
        self.override = ""
        self.retired = False
        self.sources = {}           # src -> {"cursor":..., "snapshot": bytes}
        self.snapshot_uploads = 0
        self.lost_responses = 0     # next N syncs execute, then time out

    # ── routing ──
    def install(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", self.urlopen)
        return self

    def road_requests(self, road=None, path_part=""):
        return [r for r in self.requests
                if (road is None or r["road"] == road) and path_part in r["path"]]

    def urlopen(self, req, data=None, timeout=None, **kw):
        if isinstance(req, str):
            req = urllib.request.Request(req, data=data)
        url = req.full_url
        parts = urllib.parse.urlsplit(url)
        road = HOSTS.get(parts.netloc)
        if road is None:
            raise urllib.error.URLError("fake: host %s not routed" % parts.netloc)
        ua = req.get_header("User-agent") or ""
        rec = {"road": road, "method": req.get_method(), "path": parts.path,
               "query": parts.query, "thread": threading.get_ident(),
               "ua": ua, "timeout": timeout}
        with self.lock:
            self.requests.append(rec)
        mode = self.modes[road]
        if mode == "down":
            raise urllib.error.URLError(ConnectionRefusedError(61, "refused"))
        if mode == "timeout":
            raise socket.timeout("timed out")
        if mode == "404":
            raise _err(url, 404, b'{"error": "not found"}')
        if mode == "503":
            raise _err(url, 503, b'{"error": "busy"}', [("Retry-After", "30")])
        if mode == "slow":
            time.sleep(self.slow_seconds)
        if road == "public" and (not ua or ua.startswith("Python-urllib")):
            raise _err(url, 403, b"error code: 1010")
        body = req.data if req.data is not None else data
        if body and (req.get_header("Content-encoding") or "") == "gzip":
            body = zlib.decompress(body, 16 + zlib.MAX_WBITS)
        status, out = self.handle(req.get_method(), parts.path,
                                  urllib.parse.parse_qs(parts.query),
                                  dict(req.header_items()), body)
        if parts.path.endswith("/sync") and self.lost_responses > 0:
            self.lost_responses -= 1
            raise socket.timeout("timed out (the server executed it)")
        payload = json.dumps(out).encode("utf-8")
        if status >= 400:
            raise _err(url, status, payload)
        return _Resp(status, payload, [("Content-Type", "application/json")])

    # ── the server ──
    def _authed(self, headers):
        tok = headers.get("X-lem-bench-token") or headers.get("X-LEM-Bench-Token")
        return bool(self.token) and tok == self.token

    def config_body(self):
        return {"machine_uid": self.uid, "snapshot_age_seconds": 3,
                "corrections": list(self.corrections), "qc_samples": [],
                "qc_targets": [], "qc_specs": [], "maintenance": [],
                "override": self.override, "config_rev": self.config_rev,
                "machine_config": {}, "machine": "retired" if self.retired
                else "active", "last_qc": []}

    def handle(self, method, path, query, headers, body):
        base = "/api/v2/bench/%s/" % self.uid
        if path == "/api/v2/ping":
            return 200, {"proto": [2], "version": "fake"}
        if path == base + "enroll":
            if headers.get("X-lem-token") != self.shared:
                return 401, {"error": "Not authorised."}
            doc = json.loads(body or b"{}")
            key = doc.get("enroll_key")
            if (not self.enrolled_once and self.known) or self.approved or (
                    key and key == self.enroll_key and self.token):
                if not (key and key == self.enroll_key and self.token):
                    self.token = "tok-%d" % (len(self.requests))
                self.enroll_key = key
                self.enrolled_once = True
                self.approved = False
                return 200, {"token": self.token, "machine_uid": self.uid}
            why = ("this bench is already enrolled; a new token needs a person "
                   "to approve it in LEM" if self.enrolled_once else
                   "LEM does not know this uid")
            return 202, {"state": "pending", "why": why}
        if not self._authed(headers):
            return 401, {"error": "LEM does not recognise this bench's token"}
        if path == base + "config":
            return 200, self.config_body()
        if path == base + "checkpoint":
            return 200, {"machine_uid": self.uid,
                         "epochs": [{"epoch": e, "acked": a}
                                    for e, a in self.cursor.items()],
                         "sources": [{"src": s, "cursor": v["cursor"],
                                      "snapshot": base64.b64encode(
                                          v["snapshot"]).decode()
                                      if v.get("snapshot") is not None else None}
                                     for s, v in self.sources.items()],
                         "result_ledger": [], "last_qc": []}
        if path == base + "adoption":
            # §10.2's digest: this fake LEM has recorded nothing from the
            # bench before its first v4 start, so there is no history.
            return 200, {"machine_uid": self.uid, "counts": {}, "rows": 0,
                         "qc_verdicts": {}, "first_ts": None, "recent": []}
        if path == base + "source-snapshot":
            src = query.get("src", [""])[0]
            self.sources.setdefault(src, {"cursor": {}})["snapshot"] = body
            self.snapshot_uploads += 1
            return 200, {"ok": True}
        if path == base + "sync":
            doc = json.loads(body)
            epoch, from_seq = doc["epoch"], doc["from_seq"]
            acked = self.cursor.get(epoch, 0)
            if from_seq > acked + 1:
                return 409, {"error": "cursor", "acked": acked}
            for rec in doc.get("records") or []:
                crc = rec.get("crc")
                bare = {k: v for k, v in rec.items() if k != "crc"}
                assert crc == "%08x" % (zlib.crc32(canonical(bare)) & 0xffffffff)
                if rec["seq"] <= acked:
                    self.resent += 1
                    continue
                self.records.append(bare)
                acked = rec["seq"]
            self.cursor[epoch] = acked
            rev_now = self.config_rev     # computed before ingest, as LEM does
            for rec in doc.get("records") or []:
                if rec.get("kind") == "config" and isinstance(
                        rec.get("corrections"), dict):
                    for test, value in rec["corrections"].items():
                        self.corrections = [c for c in self.corrections
                                            if c["test_name"] != test]
                        if value:
                            self.corrections.append(
                                {"machine_uid": self.uid, "test_name": test,
                                 "correction": value})
                    self.config_rev = "rev-%d" % (len(self.records) + 100)
            need = []
            for s in doc.get("sources") or []:
                held = self.sources.setdefault(s["src"], {})
                held["cursor"] = s.get("cursor") or {}
                if s.get("snapshot_sha") and held.get("sha") != s["snapshot_sha"]:
                    need.append(s["src"])
                    held["sha"] = s["snapshot_sha"]
            return 200, {"epoch": epoch, "acked": acked, "durable": 0,
                         "notes": [], "config_rev": rev_now,
                         "resolutions": [], "need_snapshot": need,
                         "machine": "retired" if self.retired else "active"}
        return 404, {"error": "no route"}

    # ── helpers for tests ──
    def runs(self):
        return [r for r in self.records if r.get("kind") == "run"]

    def lem_threads(self):
        return {r["thread"] for r in self.requests}
