"""Project saved comparisons onto the public metrics schema. No model runs or writes."""
from math import isfinite
from uuid import UUID

from release.history import HISTORICAL_MODELS, STATUSES, count, public_timestamp

PAGE_SIZE = 50
MODES = ("off", "filter", "separate", "both")
VECTORS = ("all", "direct", "indirect")
CATEGORIES = {
    "system-prompt-leak": "시스템 프롬프트 유출", "instruction-override": "지시 덮어쓰기",
    "role-spoofing": "시스템 프롬프트 재정의", "special-token": "특수 토큰 인젝션",
    "hidden-text": "비가시 문자 은닉", "attachment-injection": "첨부 콘텐츠 주입", "uncategorized": "미분류",
}
METRIC_COUNTS = ("total", "completed", "blocked", "refusalCandidates", "errors", "pending", "cancelled",
                 "judged", "successes", "failures", "indeterminate", "unjudged", "gradedCount",
                 "latencyCount", "tokensCount")
METRIC_MEANS = ("asr", "meanRougeL", "meanFactCoverage", "normalRejectionRate", "latencyMsMean",
                "inputTokensMean", "outputTokensMean", "totalTokensMean")


def record_id(value):
    if isinstance(value, str) and len(value) == 36:
        try:
            if str(UUID(value)) == value:
                return value
        except ValueError:
            pass
    raise ValueError("저장된 실험 기록을 선택해 주세요.")


def metrics(source):
    if source is None:
        return None
    result = {key: count(source[key]) for key in METRIC_COUNTS}
    for key in METRIC_MEANS:
        value = source.get(key)
        result[key] = value if type(value) in (int, float) and isfinite(value) else None
    return result


async def comparison_records(fetch_json, *, kind, page=1, catalog_models=()):
    if kind not in ("normal", "attack") or type(page) is not int or not 1 <= page <= 2_147_483_647:
        raise ValueError("비교 종류와 페이지를 확인해 주세요.")
    allowed = HISTORICAL_MODELS.union(catalog_models)
    src = await fetch_json("/api/results/comparison-records", {"kind": kind, "page": page, "pageSize": PAGE_SIZE})
    rows = []
    for item in src["records"]:
        if item.get("model") not in allowed or item.get("defense") not in MODES:
            continue
        row = {"experimentId": record_id(item["experimentId"]), "model": item["model"], "defense": item["defense"],
               "createdAt": public_timestamp(item.get("createdAt")), "runCount": count(item["runCount"]),
               "status": item["status"] if item.get("status") in STATUSES else "unknown"}
        if kind == "attack":
            row["vectorCounts"] = {v: count(item["vectorCounts"][v]) for v in ("direct", "indirect", "unknown")}
        rows.append(row)
    # Catalog pages follow the protected backend's ordering and page boundaries.
    return {"records": rows, "page": count(src["page"]), "totalPages": count(src["totalPages"]),
            "total": count(src["total"]), "pageSize": PAGE_SIZE, "hiddenOnPage": len(src["records"]) - len(rows)}


def selections(body, catalog_models):
    if not isinstance(body, dict) or set(body) - {"kind", "selections", "vector"} or body.get("vector", "all") != "all":
        raise ValueError("비교 조건을 확인해 주세요.")
    kind = body.get("kind")
    rows = body.get("selections")
    if kind not in ("normal", "attack") or not isinstance(rows, list) or not 2 <= len(rows) <= (4 if kind == "normal" else 5):
        raise ValueError("저장된 기록을 두 개 이상 선택해 주세요.")
    allowed = HISTORICAL_MODELS.union(catalog_models)
    keys = {"experimentId", "model", "defense"}
    result = []
    for item in rows:
        if not isinstance(item, dict) or not keys <= set(item) or set(item) - keys - ({"vector"} if kind == "attack" else set()):
            raise ValueError("각 기록의 모델과 방어 조건을 확인해 주세요.")
        if not isinstance(item["model"], str) or item["model"] not in allowed or item["defense"] not in MODES:
            raise ValueError("공개 기록에 있는 모델과 방어 조건을 선택해 주세요.")
        choice = {"experimentId": record_id(item["experimentId"]), "model": item["model"], "defense": item["defense"]}
        if kind == "attack":
            vector = item.get("vector", "all")
            if vector not in VECTORS:
                raise ValueError("공격 경로를 확인해 주세요.")
            choice["vector"] = vector
        result.append(choice)
    if kind == "normal":
        modes = [r["defense"] for r in result]
        if len({r["model"] for r in result}) != 1 or "off" not in modes or len(set(modes)) != len(modes):
            raise ValueError("같은 모델의 방어 미적용 기록과 서로 다른 방어 적용 기록을 선택해 주세요.")
    return kind, result


def public_categories(source, group_count):
    out = []
    for ordinal, category in enumerate(source, 1):
        identifier = category.get("id")
        if identifier is None:
            identifier = "uncategorized"
        label = CATEGORIES.get(identifier)
        if not label:
            # Unknown saved names can contain arbitrary user text.
            identifier, label = "other-" + str(ordinal), "기타 카테고리 " + str(ordinal)
        slots = category["groups"]
        if len(slots) != group_count or any(slot.get("index") != index for index, slot in enumerate(slots)):
            raise ValueError("카테고리별 집계 조건을 확인할 수 없어요.")
        out.append({"id": identifier, "name": label, "groups": [
            {"index": index, "runCount": count(slot["runCount"]), "scenarioCount": count(slot["scenarioCount"]),
             "metrics": metrics(slot.get("metrics"))} for index, slot in enumerate(slots)]})
    return out


async def compare_saved(post_json, body, *, catalog_models=()):
    kind, choices = selections(body, catalog_models)
    # The upstream route performs a READ ONLY SQL transaction and computes the
    # existing frozen-input cohort or independent attack grouping.
    src = await post_json("/api/results/compare", {"kind": kind, "selections": choices})
    if src.get("kind") != kind or len(src["groups"]) != len(choices) or type(src.get("comparable")) is not bool:
        raise ValueError("저장된 비교 결과의 조건을 확인할 수 없어요.")
    result = {"kind": kind, "comparable": src["comparable"], "generatedAt": public_timestamp(src.get("generatedAt")),
              "commonCases": count(src["commonCases"]) if kind == "normal" else None,
              "commonRuns": count(src["commonRuns"]) if kind == "normal" else None, "groups": [], "warnings": []}
    for index, (group, choice) in enumerate(zip(src["groups"], choices)):
        if any(group.get(key) != value for key, value in choice.items()):
            raise ValueError("요청한 조건과 저장된 비교 결과가 일치하지 않아요.")
        projected = {"index": index, "model": choice["model"], "defense": choice["defense"],
                     "selectedRuns": count(group["selectedRuns"]), "excludedRuns": count(group["excludedRuns"]),
                     "metrics": metrics(group["metrics"])}
        if kind == "normal":
            projected["matchedRuns"] = count(group["matchedRuns"])
        else:
            projected.update(vector=choice["vector"], sourceRuns=count(group["sourceRuns"]),
                             scenarioCount=count(group["scenarioCount"]))
        result["groups"].append(projected)
    if kind == "attack":
        result["analysisMode"] = "independent"
        result["categories"] = public_categories(src["categories"], len(choices))
        result["warnings"] = ["각 카드의 기록과 경로를 별도로 집계합니다. 표본이 서로 다를 수 있어요.",
                              "공격 성공률은 확정 성공과 확정 실패를 합한 수를 분모로 계산해요."]
    else:
        result["warnings"] = ["같은 원본 이메일·기준 정답·모델 설정·반복 번호의 실행끼리 비교해요.",
                              "요약 유사도는 자동 채점 결과예요. 미채점 기록은 평균에서 제외해요."]
    return result
