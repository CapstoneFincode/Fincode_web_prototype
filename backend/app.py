"""Same-origin BAZE proxy for the Fincode dashboard.

The API key stays on the server. The baseline deliberately adds no injection
defense; email text is passed verbatim so experiments remain reproducible.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from contextlib import asynccontextmanager
import secrets
from uuid import UUID

from backend.persistence import Persistence
from backend.deployment import is_hosted, validate_hosted
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles


ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config" / "email-baseline.json").read_text("utf-8"))
GATEWAY_BASE = "https://factchat-cloud.mindlogic.ai/v1/gateway"
MAX_BODY_BYTES = 200_000


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, **details: Any):
        self.status = status
        self.payload = {"error": {"code": code, "message": message}, **details}


def invalid(message: str) -> APIError:
    return APIError(400, "INVALID_REQUEST", message)


def usage_from(value: Any) -> dict[str, int | None]:
    usage = value if isinstance(value, dict) else {}
    details = usage.get("completion_tokens_details")
    details = details if isinstance(details, dict) else {}

    def token_count(value: Any) -> int | None:
        return value if type(value) is int and 0 <= value <= 9_007_199_254_740_991 else None

    return {
        "inputTokens": token_count(usage.get("prompt_tokens")),
        "outputTokens": token_count(usage.get("completion_tokens")),
        "totalTokens": token_count(usage.get("total_tokens")),
        "reasoningTokens": token_count(details.get("reasoning_tokens")),
    }


def origin_of(value: str) -> tuple[str, str, int | None] | None:
    """Compare origins while normalizing implicit HTTP(S) ports."""
    try:
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
        ):
            return None
        return (parts.scheme, parts.hostname.lower(), parts.port or (443 if parts.scheme == "https" else 80))
    except ValueError:
        return None


async def read_payload(request: Request) -> dict[str, Any]:
    origin = request.headers.get("origin")
    expected_origin = origin_of(f"{request.url.scheme}://{request.url.netloc}")
    if origin is not None and (origin_of(origin) is None or origin_of(origin) != expected_origin):
        raise APIError(403, "ORIGIN_REJECTED", "같은 대시보드에서 요약을 요청해 주세요.")

    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise invalid("요청 본문은 application/json 형식이어야 합니다.")

    length = request.headers.get("content-length")
    if length:
        try:
            if int(length) < 0 or int(length) > MAX_BODY_BYTES:
                raise invalid("요청 크기는 200 KB 이하여야 합니다.")
        except ValueError as exc:
            raise invalid("요청 크기가 올바르지 않습니다.") from exc

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise invalid("요청 크기는 200 KB 이하여야 합니다.")
        body.extend(chunk)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise invalid("올바른 JSON 요청을 보내 주세요.") from exc
    if not isinstance(payload, dict):
        raise invalid("JSON 객체를 보내 주세요.")
    return payload


def validate_summary(payload: dict[str, Any]) -> tuple[str, str, str]:
    if payload.get("defense", "off") != "off":
        raise APIError(400, "UNSUPPORTED_DEFENSE", "방어 기법은 아직 연결되지 않았습니다. 방어 OFF로 실행해 주세요.")
    attachments = payload.get("attachments", [])
    if (not isinstance(attachments, list) or len(attachments) > 5
            or any(not isinstance(value, str) for value in attachments)
            or len(set(attachments)) != len(attachments)):
        raise invalid("추출 완료된 첨부파일 ID를 중복 없이 최대 5개 보내 주세요.")
    try:
        for identifier in attachments:
            UUID(identifier)
    except ValueError:
        raise invalid("첨부파일 ID 형식을 확인해 주세요.") from None
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise invalid("사용할 모델을 선택해 주세요. 모델 이름은 200자 이하여야 합니다.")
    model = model.strip()
    if "codex" in model.lower():
        raise invalid("Codex 모델은 현재 요약 API에서 지원하지 않습니다.")

    email = payload.get("email")
    if not isinstance(email, dict):
        raise invalid("이메일 제목과 본문을 입력해 주세요.")
    subject = email.get("subject", "")
    body = email.get("body")
    if not isinstance(subject, str) or len(subject) > 200:
        raise invalid("제목은 200자 이하여야 합니다.")
    if not isinstance(body, str) or (not body.strip() and not attachments) or len(body) > 30_000:
        raise invalid("본문은 30,000자 이하여야 하며, 본문 또는 추출 완료된 첨부파일이 필요합니다.")
    return model, subject, body


def summary_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        text = "\n".join(
            part["text"]
            for part in content
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
        )
        if text.strip():
            return text
    refusal = message.get("refusal")
    return refusal if isinstance(refusal, str) else ""


def create_app(*, api_key: str | None = None, upstream_transport: httpx.AsyncBaseTransport | None = None, database_url: str | None = None, run_worker: bool = True, attachment_storage=None, attachment_extractor=None) -> FastAPI:
    """Allow an injected HTTP transport only for deterministic offline tests."""
    persistence = Persistence(database_url if database_url is not None else os.environ.get('DATABASE_URL', ''), CONFIG)

    @asynccontextmanager
    async def lifespan(app):
        validate_hosted(persistence.db.url if persistence.db else '')
        task = asyncio.create_task(persistence.loop(generate, configured_key)) if persistence.db and run_worker else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(lifespan=lifespan, title="Fincode email summarizer", docs_url=None, redoc_url=None, openapi_url=None)
    from backend.attachments import install_routes
    attachment_service = install_routes(app, persistence, storage=attachment_storage, extractor=attachment_extractor)
    app.state.attachments = attachment_service

    def configured_key() -> str:
        return (api_key if api_key is not None else os.environ.get("BAZE_API_KEY", "")).strip()

    @app.exception_handler(APIError)
    async def api_error_handler(_: Request, error: APIError) -> JSONResponse:
        return JSONResponse(error.payload, status_code=error.status, headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def no_store_api(request: Request, call_next):
        shared_token = os.environ.get('BACKEND_SHARED_TOKEN','')
        if request.url.path.startswith('/api/') and is_hosted():
            try:
                validate_hosted(persistence.db.url if persistence.db else '')
            except RuntimeError:
                return JSONResponse({'error':{'code':'BACKEND_NOT_CONFIGURED','message':'서버 배포 설정을 확인해 주세요.'}},status_code=503,headers={'Cache-Control':'no-store'})
        if shared_token and request.url.path.startswith('/api/') and request.url.path not in {'/api/health','/api/ready'}:
            if not secrets.compare_digest(request.headers.get('X-Fincode-Backend-Token','').encode(),shared_token.encode()):
                return JSONResponse({'error':{'code':'UNAUTHORIZED','message':'인증된 대시보드에서 접근해 주세요.'}},status_code=401,headers={'Cache-Control':'no-store'})
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def gateway(method: str, path: str, payload: Any = None, *, timeout_ms: int | None = None) -> tuple[dict[str, Any], int]:
        key = configured_key()
        if not key:
            raise APIError(503, "API_KEY_MISSING", "서버에 BAZE_API_KEY를 설정해 주세요.")
        start = time.perf_counter()
        timeout_seconds = (timeout_ms if timeout_ms is not None else CONFIG["timeoutMs"]) / 1000

        def latency() -> int:
            return max(0, round((time.perf_counter() - start) * 1000))

        try:
            async with asyncio.timeout(timeout_seconds):
                async with httpx.AsyncClient(
                    transport=upstream_transport,
                    timeout=timeout_seconds,
                    follow_redirects=False,
                ) as client:
                    response = await client.request(
                        method,
                        f"{GATEWAY_BASE}{path}",
                        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                        **({"json": payload} if payload is not None else {}),
                    )
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise APIError(504, "GATEWAY_TIMEOUT", "모델 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.", latencyMs=latency()) from exc
        except httpx.RequestError as exc:
            raise APIError(502, "GATEWAY_UNAVAILABLE", "BAZE 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.", latencyMs=latency()) from exc

        elapsed = latency()
        if response.status_code in {401, 403}:
            raise APIError(502, "GATEWAY_AUTH_ERROR", "BAZE 인증에 실패했습니다. 이 게이트웨이에서 발급한 API 키인지 확인해 주세요.", latencyMs=elapsed, upstreamStatus=response.status_code)
        if response.status_code == 429:
            raise APIError(429, "GATEWAY_RATE_LIMIT", "BAZE 사용 한도 또는 요청 빈도 제한에 도달했습니다. 잠시 후 다시 시도해 주세요.", latencyMs=elapsed, upstreamStatus=response.status_code)
        if 400 <= response.status_code < 500:
            raise APIError(422, "GATEWAY_REQUEST_REJECTED", "BAZE가 요청을 거절했습니다. 선택한 모델의 이용 가능 여부를 확인해 주세요.", latencyMs=elapsed, upstreamStatus=response.status_code)
        if not 200 <= response.status_code < 300:
            raise APIError(502, "GATEWAY_UNAVAILABLE", "BAZE 서버에서 정상 응답을 받지 못했습니다.", latencyMs=elapsed, upstreamStatus=response.status_code)
        try:
            data = response.json()
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            raise APIError(502, "GATEWAY_UNAVAILABLE", "BAZE 응답 형식을 확인할 수 없습니다.", latencyMs=elapsed, upstreamStatus=response.status_code) from exc
        if not isinstance(data, dict):
            raise APIError(502, "GATEWAY_UNAVAILABLE", "BAZE 응답 형식을 확인할 수 없습니다.", latencyMs=elapsed, upstreamStatus=response.status_code)
        data["_upstreamStatus"] = response.status_code
        return data, latency()

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "configured": bool(configured_key()),
            "provider": "baze",
            "promptVersion": CONFIG["version"],
            "features": {"summarize": True, "attachments": attachment_service.configured, "defenses": False, "experiments": bool(persistence.db)},
            "attachmentCapabilities": attachment_service.capabilities(),
            "database": await persistence.health(),
        }

    @app.get('/api/ready')
    async def ready() -> JSONResponse:
        database = await persistence.health()
        available = bool(database.get('ready'))
        return JSONResponse({'ready': available}, status_code=200 if available else 503)

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        data, elapsed = await gateway("GET", "/models/?type=llm", timeout_ms=20_000)
        entries = data.get("data")
        if not isinstance(entries, list):
            raise APIError(502, "GATEWAY_UNAVAILABLE", "BAZE 모델 목록 형식을 확인할 수 없습니다.", latencyMs=elapsed)
        result = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            model_id = entry.get("id")
            if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 200 or "codex" in model_id.lower() or model_id in seen:
                continue
            seen.add(model_id)
            result.append({"id": model_id, "ownedBy": entry.get("owned_by") if isinstance(entry.get("owned_by"), str) else None})
        return {"models": result, "provider": "baze"}

    async def generate(model, messages, max_tokens=CONFIG['maxTokens']):
        data, elapsed = await gateway("POST", "/chat/completions/", {
            "model": model, "messages": messages, "stream": False, "max_tokens": max_tokens,
        })
        usage = usage_from(data.get("usage"))
        choices = data.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        text = summary_text(choice.get("message"))
        if not text.strip():
            raise APIError(502, "EMPTY_RESPONSE", "모델이 요약 텍스트를 반환하지 않았습니다. 토큰 사용량을 확인하고 다른 모델로 시도해 주세요.", usage=usage, usageRaw=data.get("usage"), upstreamStatus=data.get("_upstreamStatus"), latencyMs=elapsed)
        finish_reason = choice.get("finish_reason") if isinstance(choice.get("finish_reason"), str) else None
        warnings = ["출력 토큰 한도에 도달하여 요약이 중간에 끝났을 수 있습니다."] if finish_reason == "length" else []
        if finish_reason == "content_filter":
            warnings.append("모델 제공자의 콘텐츠 필터가 응답에 영향을 주었어요.")
        return {
            "mode": "live",
            "summary": text,
            "model": data.get("model") if isinstance(data.get("model"), str) and data.get("model") else model,
            "requestedModel": model,
            "provider": "baze",
            "requestId": data.get("id") if isinstance(data.get("id"), str) else None,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "promptVersion": CONFIG["version"],
            "defense": "off",
            "usage": usage,
            "usageRaw": data.get("usage") if isinstance(data.get("usage"),dict) else None,
            "upstreamStatus": data.get("_upstreamStatus"),
            "latencyMs": elapsed,
            "finishReason": finish_reason,
            "warnings": warnings,
        }

    @app.post('/api/summarize')
    async def summarize(request: Request):
        payload = await read_payload(request)
        model, subject, body = validate_summary(payload)
        if not configured_key():
            raise APIError(503, 'API_KEY_MISSING', '서버에 BAZE_API_KEY를 설정해 주세요.')
        messages = [{'role':'system','content':CONFIG['systemPrompt']}, {'role':'user','content':f'제목: {subject}\n\n{body}'}]
        if payload.get('attachments'):
            if not persistence.db:
                raise APIError(503, 'DATABASE_NOT_CONFIGURED', '첨부파일 요약에는 DB 연결이 필요합니다.')
            messages = await persistence.call('summary_messages', subject, body, payload['attachments'], CONFIG)
        # Ad-hoc summaries are transient. They never enter the durable experiment
        # queue or its input/output/call logs, even when a DB is configured.
        # No replay cache or automatic retry: each explicit request calls once.
        result = await generate(model, messages)
        return {**result, 'database': {'saved': False, 'mode': 'transient'}}

    persistence.install(app, read_payload, configured_key)
    app.state.worker_once = lambda: persistence.worker_once(generate, configured_key)
    app.state.database = persistence.db

    app.mount("/", StaticFiles(directory=ROOT / "public", html=True), name="dashboard")
    return app


app = create_app()
