"""TDD for the LabCore gateway abstraction.

FakeLabCoreGateway must behave like LabCore's HTTP queue API against an
in-memory SQLite DB, so the whole suite runs offline. It mirrors the response
shapes documented in LABCORE_INTEGRATION_GUIDE.txt and implemented by
labcore_client.LabCoreClient.
"""

from labcore_gateway import FakeLabCoreGateway


def test_is_running_true_for_fake():
    gw = FakeLabCoreGateway()
    assert gw.is_running() is True


def test_raw_sql_ddl_then_insert_and_read():
    gw = FakeLabCoreGateway()
    res = gw.sql("CREATE TABLE IF NOT EXISTS widget (id INTEGER PRIMARY KEY, name TEXT)")
    assert res.get("ok") is True

    ins = gw.sql("INSERT INTO widget (name) VALUES (?)", ["gizmo"])
    assert ins.get("ok") is True
    assert ins.get("rows_affected") == 1

    read = gw.read_sql("SELECT id, name FROM widget WHERE name = ?", ["gizmo"])
    assert read.get("ok") is True
    assert read["columns"] == ["id", "name"]
    assert read["rows"] == [{"id": 1, "name": "gizmo"}]


def test_read_sql_no_match_returns_empty_rows():
    gw = FakeLabCoreGateway()
    gw.sql("CREATE TABLE t (x TEXT)")
    read = gw.read_sql("SELECT x FROM t WHERE x = ?", ["nope"])
    assert read.get("ok") is True
    assert read["rows"] == []


def test_raw_sql_error_returns_error_dict_not_raise():
    gw = FakeLabCoreGateway()
    res = gw.sql("SELECT * FROM does_not_exist")
    assert "error" in res
    assert res.get("ok") is not True


def test_core_labcore_tables_exist():
    """The fake preseeds LabCore's real QC tables so the data source can query."""
    gw = FakeLabCoreGateway()
    for table in ("samples", "sample_tests", "sample_test_results"):
        res = gw.read_sql(f"SELECT count(*) AS n FROM {table}")
        assert res.get("ok") is True, f"{table} missing: {res}"
        assert res["rows"][0]["n"] == 0


def test_gateway_usable_from_another_thread():
    """The Flask dev server is threaded; the gateway must work across threads."""
    import threading

    gw = FakeLabCoreGateway()
    gw.sql("CREATE TABLE t (x TEXT)")
    gw.sql("INSERT INTO t (x) VALUES (?)", ["hi"])

    result = {}

    def worker():
        result["read"] = gw.read_sql("SELECT x FROM t")

    th = threading.Thread(target=worker)
    th.start()
    th.join()

    assert result["read"].get("ok") is True
    assert result["read"]["rows"] == [{"x": "hi"}]


def test_named_insert_sample_and_update_cell():
    gw = FakeLabCoreGateway()
    gw.write("insert_sample", {"lab_id": "25-00123", "customer": "Acme"})
    gw.write("add_test", {"lab_id": "25-00123", "test_name": "Flash Point"})
    gw.write("update_cell", {"lab_id": "25-00123", "test_name": "Flash Point", "value": "65"})

    rows = gw.read_sql(
        "SELECT result FROM sample_tests WHERE lab_id = ? AND test_name = ?",
        ["25-00123", "Flash Point"],
    )["rows"]
    assert rows == [{"result": "65"}]

    srows = gw.read_sql("SELECT customer_name FROM samples WHERE lab_id = ?", ["25-00123"])["rows"]
    assert srows == [{"customer_name": "Acme"}]
