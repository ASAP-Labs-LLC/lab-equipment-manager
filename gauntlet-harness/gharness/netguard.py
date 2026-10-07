"""No byte leaves this process. Production LabCore is never even looked up.

Hard rule for the gate: https://labvision.asaplabs.net is the live lab
database and the gate never calls it — not to read, not to probe. Every road
out of Python is closed here:

  * `socket.getaddrinfo`  — every hostname lookup (requests, urllib3, http.client)
  * `socket.socket.connect` / `connect_ex` — anything that skipped the lookup
  * `urllib.request.urlopen` — replaced by `HServer.urlopen` when a scenario
    installs one; otherwise refused here.

Each refused attempt is RECORDED, and an attempt on a production host is a
`violation`. gate.py fails the run on any violation, even one the code under
test swallowed — a swallowed call to production is still a call to production.
"""
import socket
import urllib.error
import urllib.request

PRODUCTION_HOSTS = ("labvision.asaplabs.net",)

_STATE = {"installed": False, "attempts": [], "violations": []}


class NetworkRefused(OSError):
    pass


def _record(what, host):
    host = str(host or "")
    _STATE["attempts"].append((what, host))
    if any(h in host for h in PRODUCTION_HOSTS):
        _STATE["violations"].append((what, host))


def _host_of(url):
    try:
        from urllib.parse import urlsplit
        return urlsplit(url).netloc
    except Exception:
        return str(url)


def refuse_urlopen(req, *a, **k):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    _record("urlopen", _host_of(url))
    raise urllib.error.URLError(NetworkRefused("gate: no network (%s)" % url))


def install():
    if _STATE["installed"]:
        return
    _STATE["installed"] = True
    real_connect = socket.socket.connect

    def getaddrinfo(host, *a, **k):
        _record("getaddrinfo", host)
        raise socket.gaierror(socket.EAI_NONAME, "gate: no network (%s)" % host)

    def connect(self, address):
        if self.family == getattr(socket, "AF_UNIX", object()):
            return real_connect(self, address)
        _record("connect", address[0] if isinstance(address, tuple) else address)
        raise NetworkRefused("gate: no network (%r)" % (address,))

    def connect_ex(self, address):
        try:
            connect(self, address)
        except NetworkRefused:
            return 111
        return 0

    socket.getaddrinfo = getaddrinfo
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    urllib.request.urlopen = refuse_urlopen


def reassert():
    """lemharness replaces urlopen with its own refusal at import time; put
    ours back so attempts are recorded."""
    urllib.request.urlopen = refuse_urlopen


def attempts():
    return list(_STATE["attempts"])


def violations():
    return list(_STATE["violations"])


def record_urlopen(host):
    """HServer calls this for a host it does not route, so the attempt is
    counted the same way a socket attempt is."""
    _record("urlopen", host)
