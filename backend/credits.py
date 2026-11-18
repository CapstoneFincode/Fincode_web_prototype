"""Read-only account credit snapshots; never infer a balance from model tokens."""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from datetime import datetime, timezone
from typing import Any, Callable


CACHE_TTL_SECONDS = 60
_NULL_BALANCE = {"remaining": None, "used": None, "quota": None}
_MESSAGES = {
    "API_KEY_MISSING": "BAZE 연결 설정이 없어 잔여 크레딧을 조회할 수 없습니다.",
    "GATEWAY_AUTH_ERROR": "BAZE 인증을 확인해야 합니다. 잔여 크레딧을 조회할 수 없습니다.",
    "GATEWAY_RATE_LIMIT": "BAZE 조회 제한에 도달했습니다. 잠시 후 다시 확인해 주세요.",
    "GATEWAY_TIMEOUT": "BAZE 크레딧 조회 시간이 초과되었습니다. 잠시 후 다시 확인해 주세요.",
    "GATEWAY_REQUEST_REJECTED": "현재 BAZE 계정에서 크레딧 조회를 사용할 수 없습니다.",
    "GATEWAY_UNAVAILABLE": "BAZE 크레딧 정보를 불러오지 못했습니다. 잠시 후 다시 확인해 주세요.",
    "CREDITS_INVALID_RESPONSE": "BAZE 응답에 유효한 잔여 크레딧이 없습니다. 현재 잔액은 확인할 수 없습니다.",
}


def _number(value: Any, *, signed: bool = False) -> int | float | None:
    if type(value) not in {int, float}:
        return None
    if abs(value) > 9_007_199_254_740_991 or not math.isfinite(value) or (not signed and value < 0):
        return None
    return value


def _renewal_date(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


class CreditBalance:
    """One short-lived snapshot per configured API key, shared by concurrent GETs.

    Errors replace the prior snapshot instead of displaying an old balance as
    current. A cached result retains its original checkedAt timestamp. The
    process-local cache is only an upstream traffic bound, never HTTP caching.
    """

    def __init__(self, gateway: Callable, key_provider: Callable[[], str], error_type: type[Exception], *, clock: Callable[[], float] = time.monotonic):
        self._gateway = gateway
        self._key_provider = key_provider
        self._error_type = error_type
        self._clock = clock
        self._lock = asyncio.Lock()
        self._snapshot: dict[str, Any] | None = None
        self._expires = 0.0
        self._identity: bytes | None = None

    async def get(self) -> dict[str, Any]:
        async with self._lock:
            identity = hashlib.sha256(self._key_provider().encode()).digest()
            if self._snapshot is not None and identity == self._identity and self._clock() < self._expires:
                return {**self._snapshot, "cached": True}

            snapshot: dict[str, Any] = {
                "available": False,
                "provider": "baze",
                "unit": "credits",
                "balance": dict(_NULL_BALANCE),
                "renewalDate": None,
                "checkedAt": None,
                "cached": False,
                "cacheTtlSeconds": CACHE_TTL_SECONDS,
                "error": None,
            }
            error_code = None
            try:
                data, _ = await self._gateway("GET", "/credits/", timeout_ms=15_000)
                total = data.get("total") if data.get("object") == "credit_balance" else None
                remaining = _number(total.get("remaining"), signed=True) if isinstance(total, dict) else None
                if remaining is None:
                    error_code = "CREDITS_INVALID_RESPONSE"
                else:
                    monthly = data.get("monthly_allocated")
                    snapshot.update({
                        "available": True,
                        "balance": {"remaining": remaining, "used": _number(total.get("used")), "quota": _number(total.get("quota"))},
                        "renewalDate": _renewal_date(monthly.get("renewal_date")) if isinstance(monthly, dict) else None,
                    })
            except self._error_type as exc:
                code = getattr(exc, "payload", {}).get("error", {}).get("code")
                error_code = code if code in _MESSAGES else "GATEWAY_UNAVAILABLE"
            if error_code:
                snapshot["error"] = {"code": error_code, "message": _MESSAGES[error_code]}
            snapshot["checkedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self._snapshot = snapshot
            self._identity = identity
            self._expires = self._clock() + CACHE_TTL_SECONDS
            return dict(snapshot)
