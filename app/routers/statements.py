import json
import time
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from ..auth import AuthedUser, get_current_user
from ..db import db_conn
from ..metrics_repo import save_period_metrics
from ..pdf_parser import (
    NameMismatchError,
    StatementParseError,
    classify_rows,
    compute_cash_flow_series,
    extract_account_name,
    extract_text,
    names_match,
    parse_transactions,
    split_and_compute,
)
from ..rate_limit import limiter
from ..scoring import compute_score
from ..serializers import score_result_to_json
from ..store import pending_reviews

router = APIRouter()

# 10 MB is generous for a text-based M-Pesa statement PDF (real ones run a
# few hundred KB even at ~7,000 transactions) — this exists to stop someone
# from POSTing a huge file and burning memory/CPU before pypdf ever gets a
# chance to reject it as unparseable.
MAX_STATEMENT_BYTES = 10 * 1024 * 1024


def _group_to_json(g: dict) -> dict:
    return {
        "id": g["id"],
        "businessName": g["businessName"],
        "paybillNumber": g["paybillNumber"],
        "count": g["count"],
        "totalAmount": round(g["totalAmount"], 2),
        "sampleDetail": g["sampleDetail"],
    }


async def _notify_score_change(conn, user_id: str, delta: int, score: int) -> None:
    if delta == 0:
        return
    sign = "+" if delta > 0 else ""
    await conn.execute(
        "insert into notifications (user_id, kind, message, created_at) values (%s, %s, %s, %s)",
        (user_id, "score_change", f"Your score changed by {sign}{delta} points — now {score}.", int(time.time() * 1000)),
    )


@router.post("/v1/statements/upload")
@limiter.limit("10/minute")
async def upload_statement(
    request: Request,
    file: UploadFile,
    password: str | None = Form(default=None),
    user: AuthedUser = Depends(get_current_user),
):
    file_bytes = await file.read()
    if len(file_bytes) > MAX_STATEMENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"code": "file_too_large", "message": "That file is larger than 10 MB — a real M-Pesa statement shouldn't be."},
        )
    # Processed in memory only — never written to disk, discarded once this
    # request returns, per the "processed and discarded" promise on the upload screen.
    async with db_conn(user.id) as conn:
        profile_row = await (await conn.execute(
            "select account_name, profile_name from profiles where id = %s", (user.id,)
        )).fetchone()
        account_name = profile_row["account_name"] if profile_row else None
        profile_name = profile_row["profile_name"] if profile_row else None

        try:
            text = extract_text(file_bytes, password)

            found_name = extract_account_name(text)
            if found_name:
                if account_name is None:
                    await conn.execute(
                        "update profiles set account_name = %s, profile_name = coalesce(profile_name, %s) where id = %s",
                        (found_name, found_name, user.id),
                    )
                elif not names_match(account_name, found_name):
                    raise NameMismatchError(account_name, found_name)

            rows = parse_transactions(text)
            tagged_rows, groups = classify_rows(rows)

            # Money in/out doesn't depend on whether an ambiguous group turns
            # out to be a loan repayment, so this can be computed and stored
            # now, regardless of whether review is still pending.
            series = compute_cash_flow_series(tagged_rows)
            if series:  # don't overwrite the demo series with an empty result
                await conn.execute(
                    """
                    insert into cash_flow (user_id, series, updated_at) values (%s, %s, now())
                    on conflict (user_id) do update set series = excluded.series, updated_at = now()
                    """,
                    (user.id, json.dumps(series)),
                )

            if groups:
                session_id = uuid.uuid4().hex
                pending_reviews[session_id] = {"user_id": user.id, "rows": tagged_rows}
                return {
                    "status": "needs_review",
                    "sessionId": session_id,
                    "groups": [_group_to_json(g) for g in groups],
                }

            previous, current = split_and_compute(tagged_rows, overrides={})
        except NameMismatchError as e:
            raise HTTPException(
                status_code=422,
                detail={"code": "name_mismatch", "message": str(e), "expectedName": e.expected_name, "foundName": e.found_name},
            ) from e
        except StatementParseError as e:
            raise HTTPException(status_code=422, detail={"code": "parse_error", "message": str(e)}) from e

        await save_period_metrics(conn, user.id, previous, current)
        result = compute_score(current, previous)
        await _notify_score_change(conn, user.id, result.delta, result.score)

    return {"status": "complete", **score_result_to_json(result)}


class ClassifyAnswer(BaseModel):
    groupId: str
    isRepayment: bool


class ClassifyRequest(BaseModel):
    answers: list[ClassifyAnswer]


@router.post("/v1/statements/{session_id}/classify")
@limiter.limit("20/minute")
async def classify_statement(
    request: Request, session_id: str, body: ClassifyRequest, user: AuthedUser = Depends(get_current_user)
):
    pending = pending_reviews.get(session_id)
    # 404 both when the session never existed/expired AND when it belongs to
    # a different account — an account can't tell which by the response,
    # which is the point (no session-ownership oracle).
    if pending is None or pending["user_id"] != user.id:
        raise HTTPException(status_code=404, detail={"code": "session_not_found", "message": "This review session has expired or doesn't exist."})
    del pending_reviews[session_id]

    overrides = {a.groupId: a.isRepayment for a in body.answers}

    try:
        previous, current = split_and_compute(pending["rows"], overrides)
    except StatementParseError as e:
        raise HTTPException(status_code=422, detail={"code": "parse_error", "message": str(e)}) from e

    async with db_conn(user.id) as conn:
        await save_period_metrics(conn, user.id, previous, current)
        result = compute_score(current, previous)
        await _notify_score_change(conn, user.id, result.delta, result.score)

    return {"status": "complete", **score_result_to_json(result)}
