"""
Admin WebSocket endpoint.

ws://0.0.0.0:8000/ws/admin

- On connect: immediately sends the last known sim_status message.
- Broadcasts state changes: idle / running / recording_ready / aborted.
- Heartbeat is driven externally by main.py's lifespan task.
- Incoming messages from clients are silently ignored (keep-alive only).
"""

import json
from typing import Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


class AdminConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []
        self._last_msg: dict = {
            "type":           "sim_status",
            "state":          "idle",
            "session_id":     None,
            "webots_pid":     None,
            "sim_time_approx": 0,
            "recording_path": None,
        }

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)
        # Send current state immediately so the client is not blind on connect
        await ws.send_json(self._last_msg)

    def disconnect(self, ws: WebSocket) -> None:
        try:
            self.active.remove(ws)
        except ValueError:
            pass

    async def broadcast(self, msg: dict) -> None:
        self._last_msg = msg
        dead: list[WebSocket] = []
        for ws in list(self.active):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


# Module-level singleton used by both the WebSocket endpoint and admin REST routes
manager = AdminConnectionManager()


async def broadcast_state(
    state:            str,
    session_id:       Optional[str]  = None,
    webots_pid:       Optional[int]  = None,
    sim_time_approx:  int            = 0,
    recording_path:   Optional[str]  = None,
) -> None:
    """Convenience wrapper called by admin REST handlers and heartbeat loop."""
    await manager.broadcast(
        {
            "type":           "sim_status",
            "state":          state,
            "session_id":     session_id,
            "webots_pid":     webots_pid,
            "sim_time_approx": sim_time_approx,
            "recording_path": recording_path,
        }
    )


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/ws/admin")
async def ws_admin(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        while True:
            # We don't act on client messages, but we must receive to detect
            # disconnects and to keep the connection alive.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
