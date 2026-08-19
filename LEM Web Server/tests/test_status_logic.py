import pytest
from datetime import datetime, timedelta
from data_source import evaluate_box, BoxEvaluation
from models import BoxConfig, SampleSpec, SampleTestSpec, WatchedTarget, STATUS_GREEN, STATUS_RED, STATUS_YELLOW, STATUS_UNKNOWN
from maintenance import MaintenanceTemplate

class MockTask(MaintenanceTemplate):
    pass

def test_evaluate_box_testing_ok(mock_now, box_config, sample_spec):
    rows = [{"Lab ID": "S1", "ValUe": "10.5", "Timestamp": "2023-01-01 11:00:00"}]
    samples = {"ContextA": sample_spec}
    
    ev = evaluate_box(box_config, samples, "Lab ID", rows)
    
    assert ev.status == STATUS_GREEN
    assert ev.sub_statuses["testing"]["status"] == STATUS_GREEN
    assert ev.sub_statuses["qc"]["status"] == STATUS_GREEN # Fresh

def test_evaluate_box_qc_stale(mock_now, box_config, sample_spec):
    # Last data was 2 days ago (qc_expire = 24h)
    rows = [{"Lab ID": "S1", "ValUe": "10.5", "Timestamp": "2022-12-30 11:00:00"}]
    samples = {"ContextA": sample_spec}
    
    ev = evaluate_box(box_config, samples, "Lab ID", rows)
    
    # Testing is OK (value is good), but QC is Stale
    assert ev.sub_statuses["testing"]["status"] == STATUS_GREEN
    assert ev.sub_statuses["qc"]["status"] == STATUS_YELLOW
    assert ev.status == STATUS_YELLOW

def test_evaluate_box_maintenance_overdue(mock_now, box_config, sample_spec):
    rows = [{"Lab ID": "S1", "ValUe": "10.5", "Timestamp": "2023-01-01 11:00:00"}]
    samples = {"ContextA": sample_spec}
    
    # Create overdue task
    task = MockTask(id="t1", name="Cal Check", kind="calibration", 
                    box_uid="box1", box_title="Box 1", 
                    start_date="2022-01-01", repeat_value=1, repeat_unit="months",
                    next_due="2022-12-01", status="PENDING") # Due last month
    
    ev = evaluate_box(box_config, samples, "Lab ID", rows, maintenance_tasks=[task])
    
    assert ev.status == STATUS_RED
    assert ev.sub_statuses["calibration"]["status"] == STATUS_RED
    assert "Overdue" in ev.sub_statuses["calibration"]["reason"]

def test_context_breakdown(mock_now):
    # 2 Contexts: Diesel (Good), Gas (Bad)
    t1 = SampleTestSpec(name="Density", value_col="D", expected=10, std_dev=1)
    s1 = SampleSpec(name="Diesel", sample_id_val="D1", tests=[t1])
    s2 = SampleSpec(name="Gas", sample_id_val="G1", tests=[t1])
    
    box = BoxConfig(uid="b", title="T", csv_path="", 
                    watched_targets=[
                        WatchedTarget(sample="Diesel", test="Density"),
                        WatchedTarget(sample="Gas", test="Density")
                    ])
    
    rows = [
        {"Lab ID": "D1", "D": "10.0", "Timestamp": "2023-01-01 11:00"}, # Good
        {"Lab ID": "G1", "D": "20.0", "Timestamp": "2023-01-01 11:00"}, # Bad (expected 10 +/- 2)
    ]
    
    samples = {"Diesel": s1, "Gas": s2}
    
    ev = evaluate_box(box, samples, "Lab ID", rows)
    
    assert ev.sub_statuses["testing"]["status"] == STATUS_RED
    assert ev.context_results["Diesel"] == STATUS_GREEN
    assert ev.context_results["Gas"] == STATUS_RED
    assert ev.status == STATUS_RED
    assert "Gas" in ev.sub_statuses["testing"]["reason"] or "Gas" in ev.overall_explanation
