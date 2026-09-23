import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from .db import close_pool, open_pool
from .rate_limit import limiter
from .routers import account, auth, cash_flow, consent, lender, lender_auth, notifications, profile, push_tokens, requests, savings_goal, score, statements

# Comma-separated in production (e.g. the deployed Netlify URLs) — defaults
# to both apps' local dev servers so nothing changes for local dev. The
# lender app's Vite dev server is pinned to 5174 (vite.config.ts), and the
# Flutter app's web build to 5175 (flutter run --web-port 5175), so this
# default stays accurate instead of racing Vite's auto-increment behavior.
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:5174,http://localhost:5175").split(",") if o.strip()
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    await open_pool()
    yield
    await close_pool()


app = FastAPI(title="PesaScore API", lifespan=lifespan)

# Per-IP in-memory limiter. Fine for a single-process dev/demo backend; a real
# deployment behind Supabase should also rate-limit at the platform/gateway
# level (Supabase Auth already rate-limits sign-in attempts itself), but
# app-level limits still matter for endpoints Supabase doesn't know about,
# like statement upload.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Consumer + lender apps' dev servers (see ALLOWED_ORIGINS above) — add the
# lender app's real deployed Netlify origin once it's actually deployed.
# Never widen this to allow_origins=["*"] — this API carries financial data
# and real session cookies for two separate principal kinds now
# (allow_credentials requires an explicit origin list, not "*", anyway).
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # Baseline hardening headers — cheap, no downside, and expected by any
    # real security review. HSTS is harmless over plain HTTP in dev (browsers
    # only honor it once they've seen it over HTTPS) and becomes load-bearing
    # the moment this is served over TLS in production.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


app.include_router(auth.router)
app.include_router(score.router)
app.include_router(consent.router)
app.include_router(requests.router)
app.include_router(statements.router)
app.include_router(profile.router)
app.include_router(cash_flow.router)
app.include_router(savings_goal.router)
app.include_router(account.router)
app.include_router(notifications.router)
app.include_router(push_tokens.router)
app.include_router(lender_auth.router)
app.include_router(lender.router)


@app.get("/health")
def health():
    return {"status": "ok"}
