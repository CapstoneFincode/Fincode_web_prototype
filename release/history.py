"""Public, read-only projection of saved experiment history."""

from datetime import datetime, timezone

# Archived provider IDs that appeared in reviewed release history. Database
# fields may contain arbitrary strings and never expand the public allowlist.
HISTORICAL_MODELS = frozenset({
    "deepseek-v4.1-flash", "gemini-3.1-flash-lite", "gemini-3.5-flash",
    "gemini-3.5-flash-lite", "gemini-3.8-flash", "gpt-5.1", "gpt-6.1-sol",
    "grok-4-1-fast", "seed-2-0-lite-260428",
})
PURPOSES = {"attack", "normal", "performance"}
STATUSES = {"draft", "queued", "running", "completed", "partial", "failed", "cancelled"}
PAGE_SIZE = 5


def public_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        pass
    return None


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError("Invalid count in experiment history")
    return value


def history_page(data, allowed_models):
    total = count(data["total"])
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = count(data["page"])
    rows = data.get("experiments")
    if (not 1 <= page <= pages or data.get("pageSize") != PAGE_SIZE or
            data.get("totalPages") != pages or not isinstance(rows, list) or
            len(rows) != min(PAGE_SIZE, max(0, total - (page - 1) * PAGE_SIZE))):
        raise ValueError("Inconsistent experiment history page")
    output = []
    for row in rows:
        models = row.get("models")
        output.append({
            "purpose": row["purpose"] if row.get("purpose") in PURPOSES else None,
            "status": row["status"] if row.get("status") in STATUSES else None,
            "createdAt": public_timestamp(row.get("created_at")),
            "models": sorted({model for model in models if isinstance(model, str) and model in allowed_models})
                if isinstance(models, list) else [],
            "defense": row["defense_enabled"] if type(row.get("defense_enabled")) is bool else None,
            "finished": count(row["finished"]), "total": count(row["total"]),
        })
    return {"rows": output, "page": page, "total": total, "totalPages": pages,
            "models": sorted(allowed_models)}


async def public_history(fetch_json, *, page=1, model="", catalog_models=()):
    """fetch_json(path, params) returns parsed upstream JSON after a successful GET."""
    allowed = HISTORICAL_MODELS.union(catalog_models)
    if type(page) is not int or page < 1 or page > 2_147_483_647:
        raise ValueError("페이지 번호를 확인해 주세요.")
    if model and model not in allowed:
        raise ValueError("공개 모델 목록에서 선택해 주세요.")
    params = {"page": page, "pageSize": PAGE_SIZE}
    if model:
        params["model"] = model
    data = await fetch_json("/api/experiments", params)
    return history_page(data, allowed)
