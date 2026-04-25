"""
AI Racer — FastAPI application entry point.

Run with:
    cd server
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import pathlib
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Expose the running event loop for use by background threads that need to
# schedule coroutines (e.g. Webots monitor callbacks in api/admin.py).
_event_loop: asyncio.AbstractEventLoop | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _event_loop
    _event_loop = asyncio.get_running_loop()

    # Initialise database
    from db.models import init_db
    from config import DB_PATH

    pathlib.Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    init_db(DB_PATH)

    # Start WebSocket heartbeat background task
    task = asyncio.create_task(heartbeat_loop())

    yield

    # Cleanup
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="AI Racer Backend", version="1.0.0", lifespan=lifespan)

# ---------------------------------------------------------------------------
# CORS — allow all origins during development
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

from api.recording import router as recording_router
from api.submission import router as submission_router
from api.admin import router as admin_router
from ws.admin import router as ws_router

app.include_router(recording_router)
app.include_router(submission_router)
app.include_router(admin_router)
app.include_router(ws_router)

# ---------------------------------------------------------------------------
# Frontend static files (served last so API routes take precedence)
# ---------------------------------------------------------------------------

frontend_dir = pathlib.Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount(
        "/",
        StaticFiles(directory=str(frontend_dir), html=True),
        name="frontend",
    )


# ---------------------------------------------------------------------------
# Heartbeat loop
# ---------------------------------------------------------------------------

async def heartbeat_loop() -> None:
    """
    Every 10 seconds, if a race is running, push a heartbeat sim_status
    message to all connected admin WebSocket clients.
    """
    from ws.admin import broadcast_state
    from race.state_machine import state_machine
    from race.session import get_current_proc, get_current_session_id

    while True:
        await asyncio.sleep(10)
        if state_machine.is_running():
            proc = get_current_proc()
            pid  = proc.pid if proc else None
            await broadcast_state(
                "running",
                session_id=get_current_session_id(),
                webots_pid=pid,
            )
