"""One TLS handshake's worth of setup per process, not per call.

Reported from the lab, 2026-08-03: the server was caught at **950% CPU** — 9.5 of
12 cores, 47.5 CPU-seconds in a 5-second window.

The cause is not in this app's logic at all. The vendored `labcore_client.py` calls
module-level `requests.get(...)` / `requests.post(...)`, and every one of those
builds a brand-new `Session` → `HTTPAdapter` → `PoolManager` → `SSLContext`, which
parses certifi's 228 KB `cacert.pem` from scratch. Measured per call:

    bare requests.get   0.441 s CPU   (Windows, the lab's target platform)
    shared Session      0.009 s CPU   ← 47x cheaper

And it is genuinely parallel: OpenSSL releases the GIL, so N concurrent reads
become N cores of real CPU work rather than being serialised the way pure-Python
threads would be. That is how a handful of small reads became a whole-machine
spike.

Reducing the NUMBER of reads (see test_performance.py) shrinks the spike but does
not fix it — the per-call cost is the bug. This is the fix: one pooled Session,
created once, reused for every call.

It lives here rather than in `labcore_client.py` because that file is vendored
verbatim from LabStation and must not be edited — `_UrlClient` is the established
seam for its bugs (see `get_test_names_raw`). **The same bug is in every LabLink
app that vendors this client**, and should be fixed upstream too.
"""
import threading

import pytest


class FakeResponse:
    status_code = 200

    def __init__(self, payload=None):
        self._payload = payload if payload is not None else {"rows": []}

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class RecordingSession:
    """Stands in for `requests.Session`, counting what goes through it."""

    def __init__(self):
        self.calls = []
        self.closed = False
        self.adapters = {}

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return FakeResponse({"tests": ["Cloud Point"]})

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return FakeResponse({"rows": [{"n": 1}]})

    def mount(self, prefix, adapter):
        self.adapters[prefix] = adapter

    def close(self):
        self.closed = True


@pytest.fixture
def gw():
    from labcore_gateway import HttpLabCoreGateway
    return HttpLabCoreGateway(base_url="https://labcore.invalid")


@pytest.fixture
def wired(gw):
    """The gateway with its session replaced by a recorder."""
    session = RecordingSession()
    gw._client.session = session
    return gw, session


# ── there is exactly one session ─────────────────────────────────────────────

class TestOneSessionPerProcess:
    def test_the_session_is_created_once_and_reused(self, gw):
        assert gw._client.session is gw._client.session

    def test_it_is_a_real_requests_session(self, gw):
        import requests
        assert isinstance(gw._client.session, requests.Session)

    def test_no_new_session_is_built_per_call(self, gw, monkeypatch):
        """The whole point: `requests.get` builds a Session, an HTTPAdapter, a
        PoolManager and an SSLContext every single time."""
        import requests

        built = []
        real = requests.Session

        class Counting(real):
            def __init__(self, *a, **k):
                built.append(1)
                super().__init__(*a, **k)

        monkeypatch.setattr(requests, "Session", Counting)
        gw._client.session          # force creation under the counter
        before = len(built)
        session = RecordingSession()
        gw._client.session = session
        for _ in range(20):
            gw.read_sql("SELECT 1")
        assert len(built) == before, "a Session was built inside the request path"
        assert len(session.calls) == 20

    def test_concurrent_callers_share_one_session(self, gw):
        """The snapshot's fallback path fans out across threads. urllib3's
        PoolManager is thread-safe and the session is never mutated after
        construction, so one shared session is correct — and one per thread would
        rebuild the SSLContext per thread, which is the bug again in miniature."""
        seen = []
        lock = threading.Lock()

        def grab():
            s = gw._client.session
            with lock:
                seen.append(id(s))

        threads = [threading.Thread(target=grab) for _ in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(set(seen)) == 1, "threads built separate sessions"

    def test_the_pool_is_big_enough_for_the_fan_out(self, gw):
        """A default pool of 10 would discard connections — and so rebuild them —
        exactly when the fallback path fans out."""
        from snapshot_service import MAX_WORKERS
        adapter = gw._client.session.get_adapter("https://labcore.invalid")
        assert adapter._pool_maxsize >= MAX_WORKERS


# ── every call goes through it ───────────────────────────────────────────────

class TestEveryCallIsPooled:
    def test_read_sql(self, wired):
        gw, session = wired
        gw.read_sql("SELECT 1")
        assert [c[0] for c in session.calls] == ["POST"]

    def test_sql(self, wired):
        gw, session = wired
        gw.sql("CREATE TABLE IF NOT EXISTS x (a TEXT)")
        assert session.calls

    def test_write(self, wired):
        gw, session = wired
        gw.write("insert_sample", {"lab_id": "1"})
        assert session.calls

    def test_is_running(self, wired):
        gw, session = wired
        assert gw.is_running() is True
        assert session.calls[0][1].endswith("/api/queue/status")

    def test_get_test_names(self, wired):
        gw, session = wired
        assert gw.get_test_names() == ["Cloud Point"]
        assert [c[0] for c in session.calls] == ["GET"]

    def test_get_samples(self, wired):
        gw, session = wired
        gw.get_samples(lab_id="1")
        assert session.calls

    def test_nothing_reaches_module_level_requests(self, wired, monkeypatch):
        """The strongest form of the check: make the bare functions fatal."""
        import requests

        def forbidden(*a, **k):
            raise AssertionError("a bare requests call bypassed the session")

        monkeypatch.setattr(requests, "get", forbidden)
        monkeypatch.setattr(requests, "post", forbidden)
        gw, _session = wired
        gw.read_sql("SELECT 1")
        gw.sql("SELECT 1")
        gw.is_running()
        gw.get_test_names()
        gw.get_samples(lab_id="1")


# ── failures still behave ────────────────────────────────────────────────────

class TestFailuresAreUnchanged:
    def test_a_connection_error_is_still_an_error_dict(self, gw):
        import requests

        class Dead(RecordingSession):
            def post(self, url, **kw):
                raise requests.ConnectionError("refused")

        gw._client.session = Dead()
        res = gw.read_sql("SELECT 1")
        assert res.get("error")

    def test_is_running_is_false_when_unreachable(self, gw):
        class Dead(RecordingSession):
            def get(self, url, **kw):
                raise OSError("refused")

        gw._client.session = Dead()
        assert gw.is_running() is False

    def test_get_test_names_returns_none_when_unreachable(self, gw):
        """None means "could not ask" and [] means "nothing" — the caller falls
        back only on the former, so this distinction must survive."""
        class Dead(RecordingSession):
            def get(self, url, **kw):
                raise OSError("refused")

        gw._client.session = Dead()
        assert gw.get_test_names() is None


# ── the bare calls are gone ──────────────────────────────────────────────────

class TestNoBareCallsLeft:
    def test_the_gateway_has_no_module_level_requests_calls(self):
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "labcore_gateway.py").read_text(encoding="utf-8")
        for bad in ("requests.get(", "requests.post(", "requests.put(",
                    "requests.delete("):
            assert bad not in src, f"{bad} in labcore_gateway.py"

    def test_the_vendored_client_is_still_unedited(self):
        """The fix belongs in the subclass. If someone 'fixes' the vendored file,
        the next re-sync from LabLink silently reverts it."""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "labcore_client.py").read_text(encoding="utf-8")
        assert "requests.get(" in src, \
            "labcore_client.py was edited — it is vendored verbatim; fix _UrlClient"
