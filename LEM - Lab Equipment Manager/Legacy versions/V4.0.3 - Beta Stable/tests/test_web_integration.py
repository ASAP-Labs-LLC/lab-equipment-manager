import pytest
from web_server import apply_manual_override
from models import BoxConfig, STATUS_DEAD, STATUS_SERVICE, STATUS_GREEN, STATUS_UNKNOWN
from data_source import BoxEvaluation

def test_apply_manual_override_dead():
    box = BoxConfig(uid="b1", title="B1", csv_path="", manual_override=STATUS_DEAD)
    ev = BoxEvaluation(STATUS_GREEN, [], None, None, "All good", False, overall_explanation="All good")
    
    status, reason = apply_manual_override(box, ev)
    
    assert status == STATUS_DEAD
    assert "DEAD-LINE" in reason
    assert "Overridden to DEAD-LINE" in ev.overall_explanation
    assert "All good" in ev.overall_explanation

def test_apply_manual_override_service():
    box = BoxConfig(uid="b1", title="B1", csv_path="", manual_override=STATUS_SERVICE)
    ev = BoxEvaluation(STATUS_UNKNOWN, [], None, None, "Dunno", False, overall_explanation="Dunno")
    
    status, reason = apply_manual_override(box, ev)
    
    assert status == STATUS_SERVICE
    assert "SERVICE" in reason
    assert "Overridden to SERVICE" in ev.overall_explanation
    assert "Dunno" in ev.overall_explanation

def test_apply_manual_override_none():
    box = BoxConfig(uid="b1", title="B1", csv_path="", manual_override="")
    ev = BoxEvaluation(STATUS_GREEN, [], None, None, "All good", False, overall_explanation="All good")
    
    status, reason = apply_manual_override(box, ev)
    
    assert status == STATUS_GREEN
    assert reason == "All good"
    assert ev.overall_explanation == "All good" # Unchanged
