from .scoring import ScoreResult


def score_result_to_json(result: ScoreResult) -> dict:
    return {
        "score": result.score,
        "previousScore": result.previous_score,
        "delta": result.delta,
        "signals": [
            {
                "label": s.label,
                "value": s.value,
                "status": s.status,
                "explanation": s.explanation,
                "recommendation": s.recommendation,
            }
            for s in result.signals
        ],
        "tip": result.tip,
    }


def grant_to_json(grant: dict) -> dict:
    return {
        "id": grant["id"],
        "lenderName": grant["lender_name"],
        "expiresAt": grant["expires_at"],
    }


def consent_to_json(request: dict) -> dict:
    return {
        "id": request["id"],
        "lenderName": request["lender_name"],
        "grantDurationDays": request["grant_duration_days"],
        "willShare": request["will_share"],
        "wontShare": request["wont_share"],
    }


def notification_to_json(notification: dict) -> dict:
    return {
        "id": notification["id"],
        "kind": notification["kind"],
        "message": notification["message"],
        "createdAt": notification["created_at"],
        "read": notification["read"],
    }
