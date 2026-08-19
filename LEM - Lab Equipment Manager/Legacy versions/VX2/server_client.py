from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx


class ServerClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000/api/v1", timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self._base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def fetch_snapshot(self) -> Dict[str, Any]:
        resp = self._client.get("/snapshot")
        resp.raise_for_status()
        return resp.json()

    def list_boxes(self) -> List[Dict[str, Any]]:
        resp = self._client.get("/boxes")
        resp.raise_for_status()
        return resp.json()

    def trigger_refresh(self) -> Dict[str, Any]:
        resp = self._client.post("/refresh")
        resp.raise_for_status()
        return resp.json()

    def update_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        resp = self._client.patch("/config", json=payload)
        resp.raise_for_status()
        return resp.json()

    def update_box_layout(self, uid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        resp = self._client.patch(f"/boxes/{uid}/layout", json=payload)
        resp.raise_for_status()
        return resp.json()

    def set_override(self, uid: str, mode: str, user: str = "", note: Optional[str] = None) -> Dict[str, Any]:
        data = {"mode": mode, "user": user, "note": note}
        resp = self._client.post(f"/boxes/{uid}/override", json=data)
        resp.raise_for_status()
        return resp.json()

    def clear_override(self, uid: str, user: str = "", note: Optional[str] = None) -> Dict[str, Any]:
        data = {"user": user, "note": note}
        resp = self._client.request("DELETE", f"/boxes/{uid}/override", json=data)
        resp.raise_for_status()
        return resp.json()

    def maintenance_tasks(self) -> List[Dict[str, Any]]:
        resp = self._client.get("/maintenance/tasks")
        resp.raise_for_status()
        return resp.json()

    def maintenance_log(self) -> List[Dict[str, Any]]:
        resp = self._client.get("/maintenance/log")
        resp.raise_for_status()
        return resp.json()

    def maintenance_create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        resp = self._client.post("/maintenance/tasks", json=payload)
        resp.raise_for_status()
        return resp.json()

    def maintenance_start(self, task_id: str) -> Dict[str, Any]:
        resp = self._client.post(f"/maintenance/tasks/{task_id}/start")
        resp.raise_for_status()
        return resp.json()

    def maintenance_complete(self, task_id: str, user: str, comment: str) -> Dict[str, Any]:
        data = {"user": user, "comment": comment}
        resp = self._client.post(f"/maintenance/tasks/{task_id}/complete", json=data)
        resp.raise_for_status()
        return resp.json()

    def maintenance_comment(self, task_id: str, box_uid: str, box_title: str, comment: str, user: str = "") -> Dict[str, Any]:
        data = {"box_uid": box_uid, "box_title": box_title, "comment": comment, "user": user}
        resp = self._client.post(f"/maintenance/tasks/{task_id}/comment", json=data)
        resp.raise_for_status()
        return resp.json()

    def maintenance_delete(self, task_id: str, user: str = "", comment: str = "") -> Dict[str, Any]:
        data = {"user": user, "comment": comment}
        resp = self._client.request("DELETE", f"/maintenance/tasks/{task_id}", json=data)
        resp.raise_for_status()
        return resp.json()

    def __enter__(self) -> "ServerClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
