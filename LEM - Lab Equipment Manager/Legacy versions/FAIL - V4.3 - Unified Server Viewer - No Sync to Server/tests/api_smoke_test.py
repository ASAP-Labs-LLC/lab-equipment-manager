#!/usr/bin/env python3
"""Simple API smoke tests for the FastAPI server."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from fastapi.testclient import TestClient


def main() -> None:
    base_dir = Path(__file__).resolve().parents[1]
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    import server  # noqa: WPS433 (runtime import to ensure sys.path set)
    report_dir = base_dir / "reports_out"
    report_dir.mkdir(exist_ok=True)

    with TestClient(server.app) as client:
        resp = client.get("/state")
        resp.raise_for_status()
        state = resp.json()
        assert "boxes" in state

        resp = client.patch(
            "/settings",
            json={
                "report_dir": str(report_dir),
                "report_enabled": True,
                "report_time": "00:01",
            },
        )
        resp.raise_for_status()
        assert resp.json().get("ok")

        resp = client.get("/maintenance")
        resp.raise_for_status()
        maint = resp.json()
        assert "tasks" in maint

        resp = client.get("/reports/preview")
        resp.raise_for_status()
        preview = resp.json()
        assert preview.get("headers"), "preview missing headers"

        resp = client.post(
            "/reports/generate",
            json={"force": True, "formats": ["csv", "html"]},
        )
        resp.raise_for_status()
        payload = resp.json()
        assert payload.get("ok"), f"generation failed: {payload}"
        outputs = payload.get("outputs") or {}
        assert outputs, "server returned no output paths"
        missing = [p for p in outputs.values() if not os.path.exists(p)]
        assert not missing, f"generated paths not found: {missing}"

    print("API smoke tests passed.")


if __name__ == "__main__":
    main()
