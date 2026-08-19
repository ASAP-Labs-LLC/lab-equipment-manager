"""LEM uses the SAME login as every other LabLink app: LabCore's
`/api/login` (username + password, or an NFC card code in either field),
which returns a session token. No app-local password.

Also covers deleting a machine that a station module registered.
"""
import pytest

from labcore_gateway import FakeLabCoreGateway
from labcore_auth import LabCoreAuth
from qc_specs import QcSpecStore, QcSpec


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def gw():
    return FakeLabCoreGateway()


# ── LabCoreAuth: the shared login seam ──────────────────────────────────────

class TestLabCoreAuth:
    def test_successful_login_returns_user_and_token(self, monkeypatch):
        calls = {}

        def fake_post(url, json=None, timeout=None):
            calls["url"] = url
            calls["json"] = json
            return FakeResponse(200, {"token": "tok-1", "username": "Kaden"})

        auth = LabCoreAuth(base_url="https://labcore.example")
        monkeypatch.setattr(auth, "_post", fake_post)
        user, token, error = auth.login("kaden", "pw")
        assert (user, token, error) == ("Kaden", "tok-1", "")
        assert calls["url"] == "https://labcore.example/api/login"
        assert calls["json"] == {"username": "kaden", "password": "pw"}

    def test_bad_credentials_message(self, monkeypatch):
        auth = LabCoreAuth(base_url="https://x")
        monkeypatch.setattr(auth, "_post",
                            lambda *a, **k: FakeResponse(401, {"error": "nope"}))
        user, token, error = auth.login("kaden", "wrong")
        assert user is None and token == ""
        assert "Invalid username or password" in error

    def test_nfc_card_code_in_one_field_is_passed_through(self, monkeypatch):
        seen = {}

        def fake_post(url, json=None, timeout=None):
            seen.update(json)
            return FakeResponse(200, {"token": "t", "username": "Ryan"})

        auth = LabCoreAuth(base_url="https://x")
        monkeypatch.setattr(auth, "_post", fake_post)
        # A reader types the card code into the password field alone.
        user, _, error = auth.login("", "04A2B7C1")
        assert user == "Ryan" and error == ""
        assert seen["password"] == "04A2B7C1"

    def test_other_status_reports_the_code(self, monkeypatch):
        auth = LabCoreAuth(base_url="https://x")
        monkeypatch.setattr(auth, "_post",
                            lambda *a, **k: FakeResponse(503, {}))
        _, _, error = auth.login("a", "b")
        assert "503" in error

    def test_connection_error_is_reported_not_raised(self, monkeypatch):
        auth = LabCoreAuth(base_url="https://x")

        def boom(*a, **k):
            raise OSError("network down")

        monkeypatch.setattr(auth, "_post", boom)
        user, _, error = auth.login("a", "b")
        assert user is None
        assert "network down" in error

    def test_logout_posts_the_bearer_token(self, monkeypatch):
        seen = {}

        def fake_post(url, json=None, timeout=None, headers=None):
            seen["url"] = url
            seen["headers"] = headers
            return FakeResponse(200, {"ok": True})

        auth = LabCoreAuth(base_url="https://x")
        monkeypatch.setattr(auth, "_post", fake_post)
        auth.logout("tok-1")
        assert seen["url"] == "https://x/api/logout"
        assert seen["headers"]["Authorization"] == "Bearer tok-1"


# ── Web app wired to the shared login ───────────────────────────────────────

class StubAuth:
    def __init__(self):
        self.logged_out = []

    def login(self, username, password):
        if password == "good":
            return (username or "carduser"), "tok-9", ""
        return None, "", "Invalid username or password."

    def logout(self, token):
        self.logged_out.append(token)


@pytest.fixture
def stub_auth():
    return StubAuth()


@pytest.fixture
def client(gw, stub_auth):
    from web_app import create_app
    app = create_app(gw, authenticator=stub_auth, secret="s")
    app.config["TESTING"] = True
    return app.test_client()


class TestSharedLoginInWebApp:
    def test_login_uses_labcore_credentials(self, client):
        r = client.post("/api/login", json={"username": "kaden",
                                            "password": "good"})
        assert r.status_code == 200
        assert r.get_json()["user"] == "kaden"
        assert client.get("/api/me").get_json()["authenticated"] is True

    def test_bad_password_is_401_with_labcore_message(self, client):
        r = client.post("/api/login", json={"username": "kaden",
                                            "password": "bad"})
        assert r.status_code == 401
        assert "Invalid username or password" in r.get_json()["error"]

    def test_nfc_only_login_works(self, client):
        r = client.post("/api/login", json={"password": "good"})
        assert r.status_code == 200
        assert r.get_json()["user"] == "carduser"

    def test_logout_tells_labcore_to_destroy_the_session(self, client,
                                                         stub_auth):
        client.post("/api/login", json={"username": "k", "password": "good"})
        client.post("/api/logout")
        assert stub_auth.logged_out == ["tok-9"]
        assert client.get("/api/me").get_json()["authenticated"] is False

    def test_protected_endpoint_still_guarded(self, client):
        assert client.post("/api/qc-specs", json={}).status_code == 401
        client.post("/api/login", json={"username": "k", "password": "good"})
        # authed now: a bad body fails validation (400), not auth (401)
        assert client.post("/api/qc-specs", json={}).status_code == 400


# ── Deleting a machine a station module registered ──────────────────────────

class TestDeleteMachine:
    def seed(self, gw, uid="m1"):
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_status ("
               "machine_uid TEXT PRIMARY KEY, title TEXT, status TEXT, "
               "reason TEXT, updated_at TEXT)")
        gw.sql("INSERT INTO lem_machine_status VALUES (?,?,?,?,?)",
               [uid, "Junk " + uid, "UNKNOWN", "r", "2026-07-28T12:00:00"])
        gw.sql("CREATE TABLE IF NOT EXISTS lem_machine_log ("
               "machine_uid TEXT, ts TEXT, kind TEXT, lab_id TEXT, "
               "test_name TEXT, value TEXT, detail TEXT)")
        gw.sql("INSERT INTO lem_machine_log VALUES (?,?,?,?,?,?,?)",
               [uid, "2026-07-28T12:00:00", "run", "1", "", "", "{}"])
        QcSpecStore(gw).save(QcSpec(uid, "Cloud Point", "QC", -9.0, 0.5))

    def count(self, gw, table, uid):
        res = gw.read_sql(f"SELECT COUNT(*) n FROM {table} WHERE machine_uid=?",
                          [uid])
        return (res.get("rows") or [{"n": 0}])[0]["n"]

    def count_kind(self, gw, uid, kind):
        res = gw.read_sql("SELECT COUNT(*) n FROM lem_machine_log "
                          "WHERE machine_uid=? AND kind=?", [uid, kind])
        return (res.get("rows") or [{"n": 0}])[0]["n"]

    def test_delete_removes_status_and_specs_but_keeps_history(self, gw,
                                                               client):
        self.seed(gw)
        client.post("/api/login", json={"username": "k", "password": "good"})
        r = client.delete("/api/machines/m1")
        assert r.status_code == 200
        assert self.count(gw, "lem_machine_status", "m1") == 0
        assert self.count(gw, "lem_qc_specs", "m1") == 0
        assert self.count_kind(gw, "m1", "run") == 1          # history kept
        assert self.count_kind(gw, "m1", "config") == 1       # and audited

    def test_purge_history_also_clears_the_log(self, gw, client):
        self.seed(gw)
        client.post("/api/login", json={"username": "k", "password": "good"})
        r = client.delete("/api/machines/m1", json={"purge_history": True})
        assert r.status_code == 200
        assert self.count_kind(gw, "m1", "run") == 0          # history gone
        # Wiping a machine's history is the one action whose record must
        # survive the wipe, so the audit entry is written afterwards.
        assert self.count_kind(gw, "m1", "config") == 1

    def test_delete_requires_auth(self, gw, client):
        self.seed(gw)
        assert client.delete("/api/machines/m1").status_code == 401
        assert self.count(gw, "lem_machine_status", "m1") == 1

    def test_delete_only_touches_the_named_machine(self, gw, client):
        self.seed(gw, "m1")
        self.seed(gw, "m2")
        client.post("/api/login", json={"username": "k", "password": "good"})
        client.delete("/api/machines/m1")
        assert self.count(gw, "lem_machine_status", "m2") == 1
        assert self.count(gw, "lem_qc_specs", "m2") == 1
