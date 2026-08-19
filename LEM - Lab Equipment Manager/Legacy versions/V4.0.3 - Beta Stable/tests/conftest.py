import pytest
import sys
import os
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from models import BoxConfig, SampleSpec, SampleTestSpec, STATUS_GREEN, STATUS_RED, STATUS_YELLOW, STATUS_UNKNOWN
from maintenance import MaintenanceTemplate

@pytest.fixture
def mock_now(monkeypatch):
    class MockDatetime:
        @classmethod
        def now(cls):
            return datetime(2023, 1, 1, 12, 0, 0)
        @classmethod
        def combine(cls, d, t):
            return datetime.combine(d, t)
    
    # We need to patch the module where NOW is defined/used if possible. 
    # data_source.NOW is aliased to datetime.now.
    import data_source
    monkeypatch.setattr(data_source, 'NOW', MockDatetime.now)
    return MockDatetime.now()

@pytest.fixture
def sample_spec():
    t1 = SampleTestSpec(name="Test1", value_col="ValUe", expected=10.0, std_dev=1.0)
    return SampleSpec(name="ContextA", sample_id_val="S1", tests=[t1])

@pytest.fixture
def box_config(sample_spec):
    from models import WatchedTarget
    return BoxConfig(
        uid="box1", title="Box 1", csv_path="dummy.csv", qc_expire_hours=24,
        watched_targets=[WatchedTarget(sample="ContextA", test="Test1")]
    )
