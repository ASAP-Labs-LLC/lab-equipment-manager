"""
Comprehensive test suite for multi-dimensional equipment status framework.

Tests all acceptance criteria including:
- Severity ordering and overall derivation
- Manual override precedence
- QC sub-status logic with staleness
- Maintenance task lifecycle mapping
- Context breakdown for Testing status
"""

import pytest
from datetime import datetime, timedelta
from data_source import evaluate_box, BoxEvaluation, _compute_testing_sub_status, ParameterResult
from models import (
    BoxConfig, SampleSpec, SampleTestSpec, WatchedTarget,
    STATUS_GREEN, STATUS_RED, STATUS_YELLOW, STATUS_UNKNOWN, STATUS_SERVICE, STATUS_DEAD
)
from maintenance import MaintenanceTemplate


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def mock_task_pending_overdue():
    """Overdue PENDING calibration task."""
    return MaintenanceTemplate(
        id="t1", name="Cal Check", kind="calibration",
        box_uid="box1", box_title="Box 1",
        start_date="2022-01-01", repeat_value=1, repeat_unit="months",
        next_due="2022-12-01",  # Overdue (mock_now is 2023-01-01)
        status="PENDING"
    )


@pytest.fixture
def mock_task_in_progress():
    """IN_PROGRESS PM task."""
    return MaintenanceTemplate(
        id="t2", name="PM Service", kind="pm",
        box_uid="box1", box_title="Box 1",
        start_date="2023-01-01", repeat_value=1, repeat_unit="weeks",
        next_due="2023-01-15",  # Future
        status="IN_PROGRESS"
    )


@pytest.fixture
def mock_task_completed():
    """COMPLETED task."""
    return MaintenanceTemplate(
        id="t3", name="Cal Complete", kind="calibration",
        box_uid="box1", box_title="Box 1",
        start_date="2022-12-01", repeat_value=1, repeat_unit="months",
        next_due="2023-01-01",  # Today
        status="COMPLETED"
    )


# ============================================================================
# 1. Severity Ordering & Overall Derivation
# ============================================================================

def test_severity_red_dominates(mock_now, box_config, sample_spec):
    """RED in any sub-status forces overall RED."""
    rows = [{"Lab ID": "S1", "ValUe": "15.0", "Timestamp": "2023-01-01 11:00:00"}]  # Out of spec
    samples = {"ContextA": sample_spec}
    
    ev = evaluate_box(box_config, samples, "Lab ID", rows)
    
    # Testing will be RED (out of spec)
    assert ev.sub_statuses["testing"]["status"] == STATUS_RED
    assert ev.status == STATUS_RED
    assert "testing" in ev.overall_explanation.lower() or "critical" in ev.overall_explanation.lower()


def test_severity_yellow_over_unknown(mock_now, box_config, sample_spec):
    """YELLOW dominates UNKNOWN when no RED present."""
    # QC will be stale (YELLOW), testing will be UNKNOWN (no data)
    rows = []  # No data
    samples = {"ContextA": sample_spec}
    
    box_config.watched_targets = []  # No tests configured → Testing UNKNOWN
    
    # Override last_good_qc to simulate stale condition
    # We'll use an overdue task instead for clearer test
    task = MaintenanceTemplate(
        id="t1", name="PM", kind="pm",
        box_uid="box1", box_title="Box 1",
        start_date="2023-01-01", repeat_value=1, repeat_unit="days",
        next_due="2023-01-01",  # Due today → YELLOW
        status="PENDING"
    )
    
    ev = evaluate_box(box_config, samples, "Lab ID", rows, maintenance_tasks=[task])
    
    assert ev.sub_statuses["pm"]["status"] == STATUS_YELLOW
    assert ev.status == STATUS_YELLOW


def test_severity_all_green(mock_now, box_config, sample_spec):
    """All GREEN sub-statuses → overall GREEN.

    Calibration/PM only report a status when tasks exist (no tasks → UNKNOWN,
    which by design does not degrade overall). Supply up-to-date, not-yet-due
    tasks so every dimension is genuinely GREEN.
    """
    rows = [{"Lab ID": "S1", "ValUe": "10.0", "Timestamp": "2023-01-01 11:00:00"}]  # In spec
    samples = {"ContextA": sample_spec}
    cal = MaintenanceTemplate(id="c1", name="Cal", kind="calibration", box_uid="box1",
                              box_title="Box 1", start_date="2023-01-01", repeat_value=1,
                              repeat_unit="months", next_due="2023-06-01", status="PENDING")
    pm = MaintenanceTemplate(id="p1", name="PM", kind="pm", box_uid="box1",
                             box_title="Box 1", start_date="2023-01-01", repeat_value=1,
                             repeat_unit="months", next_due="2023-06-01", status="PENDING")

    ev = evaluate_box(box_config, samples, "Lab ID", rows, maintenance_tasks=[cal, pm])

    assert ev.sub_statuses["testing"]["status"] == STATUS_GREEN
    assert ev.sub_statuses["qc"]["status"] == STATUS_GREEN
    assert ev.sub_statuses["calibration"]["status"] == STATUS_GREEN
    assert ev.sub_statuses["pm"]["status"] == STATUS_GREEN
    assert ev.status == STATUS_GREEN


def test_severity_unknown_when_all_unknown(mock_now):
    """All UNKNOWN sub-statuses → overall UNKNOWN."""
    box = BoxConfig(uid="b", title="T", csv_path="", watched_targets=[])
    samples = {}
    rows = []
    
    ev = evaluate_box(box, samples, "Lab ID", rows)
    
    assert ev.status == STATUS_UNKNOWN


# ============================================================================
# 2. Manual Override Precedence
# ============================================================================

def test_override_forces_overall_status(mock_now, box_config, sample_spec):
    """Manual override forces overall status even when sub-statuses are RED."""
    from web_app import apply_manual_override
    
    rows = [{"Lab ID": "S1", "ValUe": "15.0", "Timestamp": "2023-01-01 11:00:00"}]  # Out of spec → RED
    samples = {"ContextA": sample_spec}
    box_config.manual_override = STATUS_SERVICE
    
    ev = evaluate_box(box_config, samples, "Lab ID", rows)
    
    # Sub-status still RED
    assert ev.sub_statuses["testing"]["status"] == STATUS_RED
    
    # Apply override
    status, reason = apply_manual_override(box_config, ev)
    
    # Overall forced to SERVICE
    assert status == STATUS_SERVICE
    assert "override" in reason.lower() or "service" in reason.lower()


def test_override_preserves_substatuses(mock_now, box_config, sample_spec):
    """Override doesn't change sub-status values."""
    from web_app import apply_manual_override
    
    rows = [{"Lab ID": "S1", "ValUe": "10.0", "Timestamp": "2023-01-01 11:00:00"}]  # In spec
    samples = {"ContextA": sample_spec}
    box_config.manual_override = STATUS_DEAD
    
    ev = evaluate_box(box_config, samples, "Lab ID", rows)
    
    # Sub-statuses GREEN before override
    assert ev.sub_statuses["testing"]["status"] == STATUS_GREEN
    
    # Apply override
    status, reason = apply_manual_override(box_config, ev)
    
    # Sub-statuses still GREEN
    assert ev.sub_statuses["testing"]["status"] == STATUS_GREEN
    assert status == STATUS_DEAD


def test_override_modifies_explanation(mock_now, box_config, sample_spec):
    """Overall explanation reflects override."""
    from web_app import apply_manual_override
    
    rows = [{"Lab ID": "S1", "ValUe": "10.0", "Timestamp": "2023-01-01 11:00:00"}]
    samples = {"ContextA": sample_spec}
    box_config.manual_override = STATUS_SERVICE
    
    ev = evaluate_box(box_config, samples, "Lab ID", rows)
    original_explanation = ev.overall_explanation
    
    status, reason = apply_manual_override(box_config, ev)
    
    # Explanation should mention override
    assert "override" in ev.overall_explanation.lower() or "service" in ev.overall_explanation.lower()
    assert original_explanation in ev.overall_explanation  # Underlying reason preserved


# ============================================================================
# 3. QC Sub-Status Logic
# ============================================================================

def test_qc_fresh_in_spec(mock_now, box_config, sample_spec):
    """Fresh in-spec data → QC GREEN."""
    rows = [{"Lab ID": "S1", "ValUe": "10.0", "Timestamp": "2023-01-01 11:00:00"}]
    samples = {"ContextA": sample_spec}
    
    ev = evaluate_box(box_config, samples, "Lab ID", rows)
    
    assert ev.sub_statuses["qc"]["status"] == STATUS_GREEN
    assert "fresh" in ev.sub_statuses["qc"]["reason"].lower()


def test_qc_stale_after_threshold(mock_now, box_config, sample_spec):
    """Data older than qc_expire_hours → QC YELLOW."""
    # Data 2 days old, threshold is 24h
    rows = [{"Lab ID": "S1", "ValUe": "10.0", "Timestamp": "2022-12-30 11:00:00"}]
    samples = {"ContextA": sample_spec}
    
    ev = evaluate_box(box_config, samples, "Lab ID", rows)
    
    assert ev.sub_statuses["qc"]["status"] == STATUS_YELLOW
    assert "stale" in ev.sub_statuses["qc"]["reason"].lower()


def test_qc_red_when_out_of_spec(mock_now, box_config, sample_spec):
    """Out-of-spec data → QC RED."""
    rows = [{"Lab ID": "S1", "ValUe": "15.0", "Timestamp": "2023-01-01 11:00:00"}]  # Out of spec
    samples = {"ContextA": sample_spec}
    
    ev = evaluate_box(box_config, samples, "Lab ID", rows)
    
    assert ev.sub_statuses["qc"]["status"] == STATUS_RED
    assert "out of spec" in ev.sub_statuses["qc"]["reason"].lower()


def test_qc_timestamp_visibility(mock_now, box_config, sample_spec):
    """QC last_good_qc timestamp is exposed."""
    rows = [{"Lab ID": "S1", "ValUe": "10.0", "Timestamp": "2023-01-01 10:30:00"}]
    samples = {"ContextA": sample_spec}
    
    ev = evaluate_box(box_config, samples, "Lab ID", rows)
    
    assert ev.last_good_qc is not None
    assert ev.last_good_qc == datetime(2023, 1, 1, 10, 30, 0)


# ============================================================================
# 4. Maintenance Task Lifecycle Mapping
# ============================================================================

def test_maintenance_cal_overdue_red(mock_now, box_config, sample_spec, mock_task_pending_overdue):
    """Overdue PENDING calibration task → Calibration RED."""
    rows = [{"Lab ID": "S1", "ValUe": "10.0", "Timestamp": "2023-01-01 11:00:00"}]
    samples = {"ContextA": sample_spec}
    
    ev = evaluate_box(box_config, samples, "Lab ID", rows, maintenance_tasks=[mock_task_pending_overdue])
    
    assert ev.sub_statuses["calibration"]["status"] == STATUS_RED
    assert "overdue" in ev.sub_statuses["calibration"]["reason"].lower()


def test_maintenance_pm_due_today_yellow(mock_now, box_config, sample_spec):
    """PM task due today → PM YELLOW."""
    task = MaintenanceTemplate(
        id="t1", name="PM Today", kind="pm",
        box_uid="box1", box_title="Box 1",
        start_date="2023-01-01", repeat_value=1, repeat_unit="days",
        next_due="2023-01-01",  # Today
        status="PENDING"
    )
    
    rows = [{"Lab ID": "S1", "ValUe": "10.0", "Timestamp": "2023-01-01 11:00:00"}]
    samples = {"ContextA": sample_spec}
    
    ev = evaluate_box(box_config, samples, "Lab ID", rows, maintenance_tasks=[task])
    
    assert ev.sub_statuses["pm"]["status"] == STATUS_YELLOW
    assert "due today" in ev.sub_statuses["pm"]["reason"].lower()


def test_maintenance_in_progress_yellow(mock_now, box_config, sample_spec, mock_task_in_progress):
    """
    NEW: IN_PROGRESS task → sub-status YELLOW.
    This tests the enhanced lifecycle state integration.
    """
    rows = [{"Lab ID": "S1", "ValUe": "10.0", "Timestamp": "2023-01-01 11:00:00"}]
    samples = {"ContextA": sample_spec}
    
    ev = evaluate_box(box_config, samples, "Lab ID", rows, maintenance_tasks=[mock_task_in_progress])
    
    assert ev.sub_statuses["pm"]["status"] == STATUS_YELLOW
    assert "in progress" in ev.sub_statuses["pm"]["reason"].lower()


def test_maintenance_completed_green(mock_now, box_config, sample_spec, mock_task_completed):
    """
    NEW: COMPLETED task → sub-status GREEN regardless of due date.
    """
    rows = [{"Lab ID": "S1", "ValUe": "10.0", "Timestamp": "2023-01-01 11:00:00"}]
    samples = {"ContextA": sample_spec}
    
    ev = evaluate_box(box_config, samples, "Lab ID", rows, maintenance_tasks=[mock_task_completed])
    
    assert ev.sub_statuses["calibration"]["status"] == STATUS_GREEN


def test_maintenance_cancelled_ignored(mock_now, box_config, sample_spec):
    """CANCELLED tasks don't affect sub-status."""
    task = MaintenanceTemplate(
        id="t1", name="Cancelled", kind="calibration",
        box_uid="box1", box_title=" Box 1",
        start_date="2022-01-01", repeat_value=1, repeat_unit="months",
        next_due="2022-12-01",  # Overdue, but cancelled
        status="CANCELLED"
    )
    
    rows = [{"Lab ID": "S1", "ValUe": "10.0", "Timestamp": "2023-01-01 11:00:00"}]
    samples = {"ContextA": sample_spec}
    
    ev = evaluate_box(box_config, samples, "Lab ID", rows, maintenance_tasks=[task])
    
    # Should be GREEN (no active calibration tasks)
    assert ev.sub_statuses["calibration"]["status"] == STATUS_GREEN


# ============================================================================
# 5. Testing Context Breakdown
# ============================================================================

def test_context_one_fail_one_pass(mock_now):
    """One context GREEN, one RED → Overall Testing RED, context map correct."""
    t1 = SampleTestSpec(name="Density", value_col="D", expected=10, std_dev=1)
    s1 = SampleSpec(name="Diesel", sample_id_val="D1", tests=[t1])
    s2 = SampleSpec(name="Gasoline", sample_id_val="G1", tests=[t1])
    
    box = BoxConfig(
        uid="b", title="T", csv_path="",
        watched_targets=[
            WatchedTarget(sample="Diesel", test="Density"),
            WatchedTarget(sample="Gasoline", test="Density")
        ]
    )
    
    rows = [
        {"Lab ID": "D1", "D": "10.0", "Timestamp": "2023-01-01 11:00"},  # In spec
        {"Lab ID": "G1", "D": "20.0", "Timestamp": "2023-01-01 11:00"},  # Out of spec
    ]
    
    samples = {"Diesel": s1, "Gasoline": s2}
    
    ev = evaluate_box(box, samples, "Lab ID", rows)
    
    assert ev.sub_statuses["testing"]["status"] == STATUS_RED
    assert ev.context_results["Diesel"] == STATUS_GREEN
    assert ev.context_results["Gasoline"] == STATUS_RED
    assert "gasoline" in ev.sub_statuses["testing"]["reason"].lower()


def test_context_grouping_by_sample_name(mock_now):
    """Context is derived from sample name."""
    t1 = SampleTestSpec(name="Test1", value_col="V", expected=10, std_dev=1)
    s1 = SampleSpec(name="Sample_A", sample_id_val="A1", tests=[t1])
    
    box = BoxConfig(
        uid="b", title="T", csv_path="",
        watched_targets=[WatchedTarget(sample="Sample_A", test="Test1")]
    )
    
    rows = [{"Lab ID": "A1", "V": "10.0", "Timestamp": "2023-01-01 11:00"}]
    samples = {"Sample_A": s1}
    
    ev = evaluate_box(box, samples, "Lab ID", rows)
    
    assert "Sample_A" in ev.context_results
    assert ev.context_results["Sample_A"] == STATUS_GREEN


def test_context_overall_reflects_worst(mock_now):
    """Overall Testing status = worst across all contexts."""
    t1 = SampleTestSpec(name="Test", value_col="V", expected=10, std_dev=1)
    s1 = SampleSpec(name="C1", sample_id_val="S1", tests=[t1])
    s2 = SampleSpec(name="C2", sample_id_val="S2", tests=[t1])
    s3 = SampleSpec(name="C3", sample_id_val="S3", tests=[t1])
    
    box = BoxConfig(
        uid="b", title="T", csv_path="",
        watched_targets=[
            WatchedTarget(sample="C1", test="Test"),
            WatchedTarget(sample="C2", test="Test"),
            WatchedTarget(sample="C3", test="Test"),
        ]
    )
    
    rows = [
        {"Lab ID": "S1", "V": "10.0", "Timestamp": "2023-01-01 11:00"},  # GREEN
        {"Lab ID": "S2", "V": "10.0", "Timestamp": "2023-01-01 11:00"},  # GREEN
        {"Lab ID": "S3", "V": "15.0", "Timestamp": "2023-01-01 11:00"},  # RED
    ]
    
    samples = {"C1": s1, "C2": s2, "C3": s3}
    
    ev = evaluate_box(box, samples, "Lab ID", rows)
    
    assert ev.sub_statuses["testing"]["status"] == STATUS_RED  # Worst = RED


def test_context_all_green(mock_now):
    """All contexts GREEN → Testing GREEN."""
    t1 = SampleTestSpec(name="Test", value_col="V", expected=10, std_dev=1)
    s1 = SampleSpec(name="C1", sample_id_val="S1", tests=[t1])
    s2 = SampleSpec(name="C2", sample_id_val="S2", tests=[t1])
    
    box = BoxConfig(
        uid="b", title="T", csv_path="",
        watched_targets=[
            WatchedTarget(sample="C1", test="Test"),
            WatchedTarget(sample="C2", test="Test"),
        ]
    )
    
    rows = [
        {"Lab ID": "S1", "V": "10.0", "Timestamp": "2023-01-01 11:00"},
        {"Lab ID": "S2", "V": "10.0", "Timestamp": "2023-01-01 11:00"},
    ]
    
    samples = {"C1": s1, "C2": s2}
    
    ev = evaluate_box(box, samples, "Lab ID", rows)
    
    assert ev.sub_statuses["testing"]["status"] == STATUS_GREEN
    assert ev.context_results["C1"] == STATUS_GREEN
    assert ev.context_results["C2"] == STATUS_GREEN
