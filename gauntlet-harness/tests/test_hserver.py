"""HServer: the bench's two roads to LEM, each with a fault it can be given.

The v4 scenarios that matter most are about the ROAD, not the code at either
end: the LAN that silently drops (T1), a response lost after the server
committed (N3's v4 shape), the old server's 404 that is the only legal reason
to fall back to legacy projection, Cloudflare's 1010 for urllib's default
agent. Each mode has to mean exactly one thing, and above all `lose_response`
must EXECUTE the request while `drop_before` must not — the whole difference
between "landed, ack lost" and "never arrived".
"""
import json
import socket
import urllib.error
import urllib.request

import pytest
from flask import Flask, jsonify, request

from gharness import netguard
from gharness.hserver import HServer


def toy_app():
    app = Flask("toy")
    app.hits = []

    @app.route("/api/v2/bench/<uid>/sync", methods=["POST"])
    def sync(uid):
        app.hits.append(("sync", uid, request.get_json()))
        return jsonify({"acked": 1})

    @app.route("/api/v2/ping")
    def ping():
        app.hits.append(("ping", request.headers.get("User-Agent")))
        return jsonify({"ok": True})
    return app


@pytest.fixture
def srv():
    app = toy_app()
    s = HServer(lambda: app)
    s.toy = app
    yield s


def call(srv, url, data=None, ua=None):
    headers = {"Content-Type": "application/json"}
    if ua:
        headers["User-Agent"] = ua
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data else None,
                                 headers=headers, method="POST" if data else "GET")
    with srv.urlopen(req, timeout=1) as r:
        return r.status, json.loads(r.read())


A = "http://192.168.1.5:5557"
B = "https://lem.asaplabs.net"


def test_up_forwards_on_both_roads(srv):
    assert call(srv, A + "/api/v2/ping") == (200, {"ok": True})
    assert call(srv, B + "/api/v2/ping") == (200, {"ok": True})
    assert srv.roads_used() == ["A", "B"]


def test_the_app_is_not_built_until_a_request_needs_it():
    built = []
    s = HServer(lambda: built.append(1) or toy_app())
    assert built == []
    call(s, B + "/api/v2/ping")
    assert built == [1]


def test_down_never_reaches_the_app(srv):
    srv.set_road("A", "down")
    with pytest.raises(urllib.error.URLError):
        call(srv, A + "/api/v2/ping")
    assert srv.toy.hits == []


def test_drop_before_times_out_without_executing(srv):
    srv.set_road("A", "drop_before")
    with pytest.raises(socket.timeout):
        call(srv, A + "/api/v2/bench/b1/sync", {"records": []})
    assert srv.toy.hits == []


def test_lose_response_executes_then_times_out(srv):
    srv.set_road("B", "lose_response")
    with pytest.raises(socket.timeout):
        call(srv, B + "/api/v2/bench/b1/sync", {"records": []})
    assert [h[0] for h in srv.toy.hits] == ["sync"]


@pytest.mark.parametrize("mode,code", [("404", 404), ("503", 503)])
def test_status_modes_answer_without_executing(srv, mode, code):
    srv.set_road("B", mode)
    with pytest.raises(urllib.error.HTTPError) as e:
        call(srv, B + "/api/v2/ping")
    assert e.value.code == code
    assert srv.toy.hits == []
    if code == 503:
        assert e.value.headers.get("Retry-After") == "30"


def test_1010_blocks_urllibs_default_agent_and_passes_lem_station(srv):
    srv.set_road("B", "1010-without-UA")
    with pytest.raises(urllib.error.HTTPError) as e:
        call(srv, B + "/api/v2/ping")
    assert e.value.code == 403 and e.value.read() == b"error code: 1010"
    with pytest.raises(urllib.error.HTTPError):
        call(srv, B + "/api/v2/ping", ua="Python-urllib/3.12")
    assert call(srv, B + "/api/v2/ping", ua="LEM-Station/4.0.0 (b1)")[0] == 200
    assert srv.toy.hits == [("ping", "LEM-Station/4.0.0 (b1)")]


def test_a_mode_for_n_requests_then_up_again(srv):
    srv.set_road("B", "503", times=1)              # F1: "LEM 503 once"
    with pytest.raises(urllib.error.HTTPError):
        call(srv, B + "/api/v2/ping")
    assert call(srv, B + "/api/v2/ping")[0] == 200


def test_an_app_error_is_an_http_error_like_urllib_raises(srv):
    with pytest.raises(urllib.error.HTTPError) as e:
        call(srv, B + "/no/such/path")
    assert e.value.code == 404


def test_unrouted_hosts_are_refused_and_production_is_a_violation(srv):
    before = len(netguard.violations())
    with pytest.raises(urllib.error.URLError):
        call(srv, "https://example.com/x")
    with pytest.raises(urllib.error.URLError):
        call(srv, "https://labvision.asaplabs.net/api/queue/status")
    assert netguard.violations()[before:] == [("urlopen", "labvision.asaplabs.net")]


def test_resent_records_are_counted_by_seq_and_by_content_key(srv):
    r = lambda seq, pk: {"kind": "run", "seq": seq, "epoch": "e1", "src": "f", "pk": pk}
    call(srv, B + "/api/v2/bench/b1/sync",
         {"machine_uid": "b1", "epoch": "e1", "records": [r(1, "k1"), r(2, "k2")]})
    # same seq again (a resend after a lost ack), and a new epoch carrying an
    # old content key (a wiped journal re-reading the file: T4's flood)
    call(srv, B + "/api/v2/bench/b1/sync",
         {"machine_uid": "b1", "epoch": "e1", "records": [r(2, "k2"), r(3, "k3")]})
    call(srv, B + "/api/v2/bench/b1/sync",
         {"machine_uid": "b1", "epoch": "e2",
          "records": [dict(r(1, "k1"), epoch="e2")]})
    assert srv.v2_syncs() == 3
    assert (srv.records_received, srv.records_resent) == (5, 2)


def test_a_gzipped_sync_is_read_too(srv):
    """The bench gzips a sync body over 64 KB (Content-Encoding: gzip) —
    which is exactly the sync a long outage's backlog produces. The counter
    used to json-parse the raw bytes, fail, and skip the body without a
    word: every resend of a big catch-up read as 0 (T2L found it, at
    records_resent 0 with hundreds re-sent). A body it cannot read is now a
    harness error, never a silent 0."""
    import gzip
    r = lambda seq: {"kind": "run", "seq": seq, "epoch": "e1"}
    for _ in range(2):
        body = gzip.compress(json.dumps(
            {"machine_uid": "b1", "epoch": "e1",
             "records": [r(1), r(2)]}).encode())
        req = urllib.request.Request(
            B + "/api/v2/bench/b1/sync", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Content-Encoding": "gzip"})
        try:                       # the toy app reads no gzip; the counter must
            srv.urlopen(req, timeout=1).close()
        except urllib.error.HTTPError:
            pass
    assert (srv.records_received, srv.records_resent) == (4, 2)


def test_an_unreadable_sync_body_is_a_harness_error(srv):
    req = urllib.request.Request(B + "/api/v2/bench/b1/sync", data=b"\x00nope",
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    from gharness import hserver
    before = len(hserver.OBSERVE_ERRORS)
    with pytest.raises(RuntimeError):
        srv.urlopen(req, timeout=1)
    # ...and is recorded where the gate reports it (the bench's uploader
    # would swallow the raise as a dark road).
    assert len(hserver.OBSERVE_ERRORS) == before + 1
    del hserver.OBSERVE_ERRORS[before:]


def test_bad_road_or_mode_is_refused(srv):
    with pytest.raises(ValueError):
        srv.set_road("C", "up")
    with pytest.raises(ValueError):
        srv.set_road("A", "sideways")
