"""Dev entry point — use this instead of `uvicorn app.main:app` directly.

Windows defaults asyncio to ProactorEventLoop, and uvicorn.run() creates that
loop (via its own internal asyncio.run()) *before* it imports app.main — so
setting the event loop policy inside the app package itself is too late.
psycopg's async pool (app/db.py) needs a selector-based loop, so the policy
has to be set here, before uvicorn ever starts.
"""

import asyncio
import os
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    # On Windows, uvicorn's reloader stops the old worker with a Ctrl+C
    # console event. A process started without a console (a background job,
    # a CI runner, an agent's shell) never receives it, so after
    # "Reloading..." the old worker keeps serving stale code forever. Set
    # RELOAD=0 there and restart by hand instead.
    reload = os.environ.get("RELOAD", "1") != "0"
    # HOST=0.0.0.0 to let a test phone on the same Wi-Fi reach this PC
    # (debug APK built with API_BASE_URL=http://<PC's LAN IP>:8000).
    host = os.environ.get("HOST", "127.0.0.1")
    # Without reload (no subprocess), uvicorn on Windows hard-codes
    # ProactorEventLoop and ignores the policy set above, which psycopg can't
    # use — the pool just times out. Naming the loop class covers both modes.
    uvicorn.run("app.main:app", host=host, port=8000, reload=reload, loop="asyncio:SelectorEventLoop")
