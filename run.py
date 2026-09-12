"""Dev entry point — use this instead of `uvicorn app.main:app` directly.

Windows defaults asyncio to ProactorEventLoop, and uvicorn.run() creates that
loop (via its own internal asyncio.run()) *before* it imports app.main — so
setting the event loop policy inside the app package itself is too late.
psycopg's async pool (app/db.py) needs a selector-based loop, so the policy
has to be set here, before uvicorn ever starts.
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
