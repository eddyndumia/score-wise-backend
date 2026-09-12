import asyncio
import sys

from dotenv import load_dotenv

# Must run before any submodule (db.py, auth.py, routers/auth.py) reads
# os.environ at import time.
load_dotenv()

# Windows defaults asyncio to ProactorEventLoop, which psycopg's async driver
# (app/db.py's connection pool) can't run under — it needs a selector-based
# loop. This is a defensive no-op for `uvicorn app.main:app` specifically:
# uvicorn.run() creates its event loop *before* importing this package, so
# setting the policy here is too late for that path — see run.py, the actual
# fix, which sets it before uvicorn ever starts. Kept here too for any other
# entry point (tests, a one-off `python -c "import app.main"`) that imports
# this package before creating its own loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
