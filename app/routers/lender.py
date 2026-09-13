import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg.errors import InsufficientPrivilege, NoDataFound
from pydantic import BaseModel

from ..consent_categories import filter_signals
from ..db import db_conn
from ..lender_auth import AuthedLender, get_current_lender
from ..metrics_repo import load_period_metrics
from ..rate_limit import limiter
from ..scoring import compute_score

router = APIRouter()

RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _mask_email(email: str | None) -> str:
    if not email or "@" not in email:
        return "unknown"
    local, domain = email.split("@", 1)
    visible = local[:3]
    return f"{visible}{'*' * max(len(local) - len(visible), 3)}@{domain}"


def _flag_and_status(score: int) -> tuple[str, str]:
    if score >= 650:
        return "low", "Approved"
    if score >= 450:
        return "moderate", "Review"
    return "high", "Declined"


async def _score_grant(conn, grant: dict):
    """Shared by _load_applicant and the dashboard's averaging — one place
    that loads a grant's borrower metrics and computes the real score."""
    profile_row = await (await conn.execute(
        "select email, created_at from profiles where id = %s", (grant["user_id"],)
    )).fetchone()
    current, previous = await load_period_metrics(conn, grant["user_id"])
    return profile_row, compute_score(current, previous)


def _applicant_dict(grant: dict, profile_row: dict | None, result) -> dict:
    will_share = grant["will_share"] or []
    flag, status = _flag_and_status(result.score)
    account_age_days = None
    if profile_row and "account_age" in will_share:
        account_age_days = (
            int((time.time() - profile_row["created_at"].timestamp()) / 86400) if profile_row["created_at"] else None
        )

    return {
        "ref": str(grant["id"]),
        "maskedId": _mask_email(profile_row["email"] if profile_row else None),
        "score": result.score,
        "flag": flag,
        "status": status,
        "appliedOn": grant["created_at"].date().isoformat(),
        "accountAgeDays": account_age_days,
        "signals": [
            {"label": s.label, "value": s.value, "status": s.status, "explanation": s.explanation}
            for s in filter_signals(result.signals, will_share)
        ],
    }


async def _load_applicant(conn, grant: dict) -> dict:
    profile_row, result = await _score_grant(conn, grant)
    return _applicant_dict(grant, profile_row, result)


class ConsentRequestBody(BaseModel):
    borrowerEmail: str
    grantDurationDays: int = 30


@router.post("/v1/lender/consent-requests")
@limiter.limit("20/minute")
async def create_consent_request(request: Request, body: ConsentRequestBody, lender: AuthedLender = Depends(get_current_lender)):
    async with db_conn(lender.id) as conn:
        try:
            row = await (await conn.execute(
                "select * from create_lender_consent_request(%s, %s)",
                (body.borrowerEmail, body.grantDurationDays),
            )).fetchone()
        except (InsufficientPrivilege, NoDataFound) as e:
            # The function raises for both "not a registered lender"
            # (shouldn't happen — get_current_lender already checked) and
            # "no borrower with that email" — a 404 either way, deliberately
            # not distinguishing further (see the project plan's "email
            # enumeration" trade-off note). The function also inserts the
            # borrower's notification itself (same security-definer
            # boundary as the pending_consents insert — the lender's own
            # restricted role can't write a row it doesn't own).
            raise HTTPException(status_code=404, detail="No PesaScore borrower account found for that email") from e
    return {"ok": True, "requestId": str(row["id"])}


@router.get("/v1/lender/applicants")
async def list_applicants(lender: AuthedLender = Depends(get_current_lender)):
    async with db_conn(lender.id) as conn:
        grants = await (await conn.execute(
            "select id, user_id, created_at, will_share from grants_table"
            " where lender_id = %s and expires_at > %s order by created_at desc",
            (lender.id, _now_ms()),
        )).fetchall()
        return [await _load_applicant(conn, g) for g in grants]


@router.get("/v1/lender/applicants/{grant_id}")
async def get_applicant(grant_id: str, lender: AuthedLender = Depends(get_current_lender)):
    async with db_conn(lender.id) as conn:
        grant = await (await conn.execute(
            "select id, user_id, created_at, will_share from grants_table"
            " where lender_id = %s and id = %s and expires_at > %s",
            (lender.id, grant_id, _now_ms()),
        )).fetchone()
        if grant is None:
            raise HTTPException(status_code=404, detail="Applicant not found")
        return await _load_applicant(conn, grant)


@router.get("/v1/lender/dashboard")
async def get_dashboard(range: str = Query("30d", pattern="^(7d|30d|90d)$"), lender: AuthedLender = Depends(get_current_lender)):
    since_ms = _now_ms() - RANGE_DAYS[range] * 24 * 60 * 60 * 1000
    async with db_conn(lender.id) as conn:
        grants = await (await conn.execute(
            "select id, user_id, created_at, will_share, expires_at from grants_table"
            " where lender_id = %s and created_at >= to_timestamp(%s / 1000.0) order by created_at",
            (lender.id, since_ms),
        )).fetchall()

        active_grants = [g for g in grants if g["expires_at"] > _now_ms()]
        scored = [await _score_grant(conn, g) for g in active_grants]
        applicants = [_applicant_dict(g, profile_row, result) for g, (profile_row, result) in zip(active_grants, scored)]

    scores = [a["score"] for a in applicants]
    previous_scores = [result.previous_score for _, result in scored]
    approved = sum(1 for a in applicants if a["status"] == "Approved")
    review = sum(1 for a in applicants if a["status"] == "Review")
    declined = sum(1 for a in applicants if a["status"] == "Declined")
    flagged = sum(1 for a in applicants if a["flag"] == "high")

    # Sun=0 .. Sat=6, matching the frontend's existing day labels. Based on
    # every grant created in range (not just currently-active ones) — this
    # is a history of when requests arrived, not a snapshot of who's active
    # right now. Python's weekday() is Monday=0..Sunday=6; (weekday+1)%7
    # remaps it to Sunday=0..Saturday=6.
    day_labels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    day_counts = [0] * 7
    for g in grants:
        day_counts[(g["created_at"].weekday() + 1) % 7] += 1
    busiest_index = day_counts.index(max(day_counts)) if any(day_counts) else 0

    return {
        "miniStats": {
            "scoresRun": len(applicants),
            "applicants": len(applicants),
            "flagged": flagged,
            "approved": approved,
        },
        # No real per-day score history exists anywhere in this schema (only
        # a current/previous snapshot per borrower) — a fabricated smooth
        # sparkline was deliberately dropped in favor of this real, coarser
        # avg-current-vs-avg-previous comparison. See the project plan's
        # "dashboard sparkline" decision.
        "avgScore": round(sum(scores) / len(scores)) if scores else None,
        "avgPreviousScore": round(sum(previous_scores) / len(previous_scores)) if previous_scores else None,
        "avgDelta": (
            round(sum(scores) / len(scores)) - round(sum(previous_scores) / len(previous_scores))
            if scores and previous_scores
            else None
        ),
        "segments": [
            {"label": "Approved", "count": approved, "color": "green"},
            {"label": "Manual review", "count": review, "color": "amber"},
            {"label": "Declined", "count": declined, "color": "coral"},
        ],
        "busiestDay": {
            "days": [{"label": label, "count": count} for label, count in zip(day_labels, day_counts)],
            "activeIndex": busiest_index,
        },
        "gauge": {
            "percent": round(100 * approved / len(applicants)) if applicants else 0,
            "sub": "On track for 70% target",
        },
    }
