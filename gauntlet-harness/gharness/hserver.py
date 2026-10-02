"""HServer: the LEM web server, reached by the bench through a patched urlopen.

Spec §15.2. The bench's two roads are routed by host:

    192.168.1.5:5557   -> road "A" (LAN; dark in production today)
    lem.asaplabs.net   -> road "B" (public, Cloudflare tunnel)

and each road has a mode:

    up                forwarded to the Flask app (test client), answered as-is
    down              connection refused; nothing reaches the app
    drop_before       the request times out before the app gets it
    lose_response     the app EXECUTES it, then the client times out — the
                      write landed and the bench cannot know (N3's shape)
    404               "Not Found" without reaching the app (an old server)
    503               "Service Unavailable" + Retry-After, app not reached
    1010-without-UA   Cloudflare's browser-integrity check: a request with no
                      User-Agent of its own (urllib's default
                      "Python-urllib/x.y") gets 403 "error code: 1010"; one
                      that sends `LEM-Station/...` is forwarded

A mode can be set for the next N requests only (`times=`), after which the
road goes back to `up` — that is how "LEM 503 once" (F1) is said.

Anything else is refused, and a production host is recorded as a violation by
netguard. The server is built lazily, on the first routed request, so a bench
that never calls out (v3.9 with no `lem_meta.live_url`, every baseline
scenario) pays nothing and changes no LabCore op count.

What HServer counts: requests per road and per path, v2 sync bodies, and
`records_resent` — run records whose `(uid, epoch, seq)` or `(uid, src, pk)`
was already received in an earlier request (spec §6.1 / T4).
"""
import email.message
import io
import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

from . import netguard

ROAD_HOSTS = {"192.168.1.5:5557": "A", "lem.asaplabs.net": "B"}
MODES = ("up", "down", "drop_before", "lose_response", "404", "503",
         "1010-without-UA")


class _Response(io.BytesIO):
    """What urlopen hands back: readable, a context manager, with status."""

    def __init__(self, status, body, headers, url):
        super().__init__(body)
        self.status = status
        self.code = status
        self.url = url
        self.headers = headers
        self.reason = "OK"

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def info(self):
        return self.headers

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _headers(pairs):
    m = email.message.Message()
    for k, v in pairs:
        m[k] = v
    return m


def _http_error(url, code, msg, body=b"", extra=()):
    hdrs = _headers(list(extra) + [("Content-Type", "text/plain")])
    return urllib.error.HTTPError(url, code, msg, hdrs, io.BytesIO(body))


class HServer:
    def __init__(self, app_factory):
        """`app_factory()` builds the Flask app on first use."""
        self._factory = app_factory
        self._app = None
        self._client = None
        self.modes = {"A": "up", "B": "up"}
        self._remaining = {"A": None, "B": None}
        self.requests = Counter()          # (road, method, path) -> n
        self.outcomes = Counter()          # (road, outcome) -> n
        self.executed = Counter()          # road -> requests the app ran
        self.sync_bodies = []
        self._seen_seq = set()
        self._seen_pk = set()
        self.records_received = 0
        self.records_resent = 0
        # Which thread made each request. `poll_thread` is the thread the
        # harness polls on; a request from it is LEM I/O on the poll, which
        # transfer §6.3 forbids (`poll_thread_requests` must stay 0).
        self.poll_thread = None
        self.poll_thread_requests = 0
        self.request_threads = Counter()
        self.user_agents = Counter()

    # ── configuration ──
    def set_road(self, road, mode, times=None):
        if road not in self.modes:
            raise ValueError("road must be A or B, not %r" % (road,))
        if mode not in MODES:
            raise ValueError("mode must be one of %s, not %r" % (MODES, mode))
        self.modes[road] = mode
        self._remaining[road] = times

    def set_roads(self, mode, times=None):
        for r in self.modes:
            self.set_road(r, mode, times)

    @property
    def app(self):
        if self._app is None:
            self._app = self._factory()
            self._client = self._app.test_client()
        return self._app

    # ── the patched urlopen ──
    def install(self):
        urllib.request.urlopen = self.urlopen
        return self

    def urlopen(self, req, data=None, timeout=None, **kw):
        if isinstance(req, str):
            req = urllib.request.Request(req, data=data)
        url = req.full_url
        parts = urllib.parse.urlsplit(url)
        road = ROAD_HOSTS.get(parts.netloc)
        if road is None:
            netguard.record_urlopen(parts.netloc)
            raise urllib.error.URLError(
                netguard.NetworkRefused("gate: host not routed (%s)" % parts.netloc))
        mode = self.modes[road]
        if self._remaining[road] is not None:
            self._remaining[road] -= 1
            if self._remaining[road] <= 0:
                self.modes[road] = "up"
                self._remaining[road] = None
        method = req.get_method()
        path = parts.path + (("?" + parts.query) if parts.query else "")
        ident = threading.get_ident()
        self.request_threads[ident] += 1
        if self.poll_thread is not None and ident == self.poll_thread:
            self.poll_thread_requests += 1
        self.user_agents[(req.get_header("User-agent") or "")[:40]] += 1
        self.requests[(road, method, parts.path)] += 1
        self.outcomes[(road, mode)] += 1
        if mode == "down":
            raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
        if mode == "drop_before":
            raise socket.timeout("timed out")
        if mode == "404":
            raise _http_error(url, 404, "NOT FOUND", b'{"error": "not found"}')
        if mode == "503":
            raise _http_error(url, 503, "SERVICE UNAVAILABLE",
                              b'{"error": "unavailable"}', [("Retry-After", "30")])
        if mode == "1010-without-UA":
            ua = req.get_header("User-agent") or ""
            if not ua or ua.startswith("Python-urllib"):
                raise _http_error(url, 403, "Forbidden", b"error code: 1010")
        body = req.data if req.data is not None else data
        self._observe(method, parts.path, body)
        hdrs = {k: v for k, v in req.header_items()}
        resp = self._client_for().open(
            path, method=method, data=body, headers=hdrs,
            base_url="%s://%s" % (parts.scheme, parts.netloc))
        self.executed[road] += 1
        if mode == "lose_response":
            raise socket.timeout("timed out")
        status = resp.status_code
        payload = resp.get_data()
        if status >= 400:
            raise _http_error(url, status, resp.status, payload,
                              list(resp.headers.items()))
        return _Response(status, payload, _headers(list(resp.headers.items())), url)

    def _client_for(self):
        self.app
        return self._client

    # ── what the server was sent ──
    def _observe(self, method, path, body):
        if method != "POST" or not path.startswith("/api/v2/bench/") \
                or not path.endswith("/sync"):
            return
        try:
            doc = json.loads(body or b"{}")
        except Exception:
            return
        self.sync_bodies.append(doc)
        uid = doc.get("machine_uid")
        epoch = doc.get("epoch")
        for rec in doc.get("records") or []:
            if not isinstance(rec, dict) or rec.get("kind") != "run":
                continue
            self.records_received += 1
            k_seq = (uid, rec.get("epoch", epoch), rec.get("seq"))
            k_pk = (uid, rec.get("src"), json.dumps(rec.get("pk"), sort_keys=True))
            again = k_seq in self._seen_seq or (
                rec.get("pk") is not None and k_pk in self._seen_pk)
            if again:
                self.records_resent += 1
            self._seen_seq.add(k_seq)
            if rec.get("pk") is not None:
                self._seen_pk.add(k_pk)

    def v2_syncs(self):
        return sum(n for (road, m, p), n in self.requests.items()
                   if m == "POST" and p.startswith("/api/v2/bench/")
                   and p.endswith("/sync"))

    def roads_used(self):
        """Roads on which the app actually executed a request."""
        return sorted(r for r, n in self.executed.items() if n)
