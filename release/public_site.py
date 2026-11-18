"""Anonymous release frontend, with a narrow contract to the authenticated lab API."""
import asyncio
import json
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.deployment import server_port
from backend.summary_quality import evaluate_summary
from release.history import HISTORICAL_MODELS, public_history
from release.matched import compare_saved, comparison_records

ROOT = Path(__file__).resolve().parents[1]
MODES = ("off", "filter", "separate", "both")
MODELS = (
    "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "deepseek-v4.1-flash", "gemini-3.5-flash",
    "grok-4-1-fast", "gemini-3.8-flash", "seed-2-0-lite-260428",
)
PUBLIC_SAMPLES = json.loads((ROOT / "datasets/ground_truth_set/v1/emails.json").read_text(encoding="utf-8"))
MAX_REQUEST_BYTES = 36_000


class PublicError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class Gateway:
    def __init__(self, url, token, transport):
        parsed = urlsplit(url)
        private = parsed.scheme == "http" and (parsed.hostname or "").endswith(".railway.internal")
        if not ((parsed.scheme == "https" or private) and parsed.hostname and not parsed.username
                and not parsed.password and parsed.path in ("", "/") and not parsed.query and not parsed.fragment):
            raise ValueError("BACKEND_API_URL must be a trusted HTTPS origin or a private Railway HTTP origin")
        if not token:
            raise ValueError("Missing BACKEND_SHARED_TOKEN")
        self.client = httpx.AsyncClient(base_url=url.rstrip("/"), headers={"X-Fincode-Backend-Token": token},
                                        timeout=130, follow_redirects=False, transport=transport)
        self.cache = {}
        self.locks = {}
        self.recent_calls = deque()
        self.active = 0

    async def request(self, method, path, **kwargs):
        try:
            response = await self.client.request(method, path, **kwargs)
            if response.status_code == 429:
                raise PublicError(429, "서비스 호출 한도에 도달했어요. 잠시 후 다시 시도해 주세요.")
            if response.status_code == 422:
                raise PublicError(422, "선택한 모델이 요청을 처리할 수 없어요. 다른 모델로 시도해 주세요.")
            if not response.is_success:
                raise PublicError(502, "연결된 서비스가 요청을 처리하지 못했어요.")
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except httpx.TimeoutException:
            raise PublicError(504, "응답 대기 시간을 초과했어요. 잠시 후 다시 시도해 주세요.") from None
        except (httpx.RequestError, ValueError):
            raise PublicError(502, "연결된 서비스의 응답을 확인할 수 없어요.") from None

    async def post(self, path, data):
        return await self.request("POST", path, json=data)

    async def read(self, path, params=None):
        return await self.request("GET", path, params=params)

    async def cached(self, key, load, ttl=60):
        # Call sites use only finite server-selected keys, never user-provided identifiers.
        async with self.locks.setdefault(key, asyncio.Lock()):
            entry = self.cache.get(key)
            if entry and entry[0] > time.monotonic():
                return entry[1]
            value = await load()
            self.cache[key] = (time.monotonic() + ttl, value)
            return value

    async def models(self):
        async def load():
            data = await self.read("/api/models")
            available = {m.get("id") for m in data["models"] if isinstance(m, dict)}
            return [model for model in MODELS if model in available]
        return await self.cached("models", load, ttl=120)

    async def samples(self):
        async def load():
            dataset, raw = await asyncio.gather(self.read("/api/performance/dataset"), self.read("/api/data"))
            reviewed = {item["sourceId"]: item for item in dataset["dataset"]["items"]
                        if item.get("reviewStatus") == "reviewed" and item.get("referenceReviewStatus") == "reviewed"}
            normals = {item["dbId"]: item for item in raw["normal"] if item.get("reviewStatus") == "reviewed"
                       and item.get("referenceReviewStatus") == "reviewed"}
            selected = {}
            for sample in PUBLIC_SAMPLES:
                approved = reviewed.get(sample["id"], {})
                row = normals.get(approved.get("id"))
                if (row and approved["dbVersion"] == row["dbVersion"] and approved["groundTruthId"] == row["groundTruthId"]
                    and all(row.get(field) == sample[field] for field in ("title", "body", "reference", "requiredFacts"))):
                    selected[sample["id"]] = sample
            return selected
        return await self.cached("samples", load)

    async def summarize(self, model, subject, body, defense):
        now = time.monotonic()
        while self.recent_calls and self.recent_calls[0] <= now - 3600:
            self.recent_calls.popleft()
        if self.active >= 4 or len(self.recent_calls) >= 120:
            raise PublicError(429, "공개 실험의 동시 실행 또는 시간당 한도에 도달했어요. 잠시 후 다시 시도해 주세요.")
        self.recent_calls.append(now)
        self.active += 1
        try:
            data = await self.request("POST", "/api/summarize", json={
                "model": model, "email": {"subject": subject, "body": body}, "defense": defense})
        finally:
            self.active -= 1
        summary = data.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise PublicError(502, "모델이 요약문을 반환하지 않았어요.")
        return {"summary": summary, "model": data.get("model") or model, "defense": defense,
                "blocked": data.get("blocked") is True, "latencyMs": data.get("latencyMs"),
                "usage": {key: (data.get("usage") or {}).get(key)
                          for key in ("inputTokens", "outputTokens", "totalTokens", "reasoningTokens")}}


def project(mapping, fields):
    return {field: mapping.get(field) for field in fields.split()}


def comparison_view(raw):
    models = []
    for item in raw["models"]:
        if item.get("model") not in HISTORICAL_MODELS.union(MODELS):
            continue
        models.append({**project(item, "model total blocked latencyMsMean"),
                       "tokens": project(item["tokens"], "totalMean"),
                       "attack": project(item["attack"], "successes failures judged asr") if item.get("attack") else None,
                       "normal": project(item["normal"], "judged rejected fpr") if item.get("normal") else None,
                       "quality": project(item["quality"], "gradedCount meanRougeL") if item.get("quality") else None})
    return {**project(raw, "purpose vector defense generatedAt"), "models": models}


def overview_view(raw):
    dataset = raw["datasets"]
    return {"datasets": {**project(dataset, "normal attacks direct indirect categories"),
                         "groundTruth": project(dataset["groundTruth"], "total ready")},
            "experiments": project(raw["experiments"], "total active"),
            "performance": project(raw["performance"], "total completed modelCount")}


async def payload(request, fields, optional=frozenset()):
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            host = request.headers.get("host", "").lower()
            if parsed.scheme not in ("https", "http") or parsed.netloc.lower() != host or parsed.path or parsed.query or parsed.fragment:
                raise ValueError()
        except ValueError:
            raise PublicError(403, "같은 웹사이트에서 요청해 주세요.") from None
    if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json":
        raise PublicError(415, "JSON 형식으로 요청해 주세요.")
    buf = bytearray()
    async for chunk in request.stream():
        if len(buf) + len(chunk) > MAX_REQUEST_BYTES:
            raise PublicError(413, "요청이 너무 커요.")
        buf.extend(chunk)
    try:
        data = json.loads(buf)
    except (ValueError, UnicodeError, RecursionError):
        raise PublicError(400, "요청 내용을 읽을 수 없어요.") from None
    if not isinstance(data, dict) or not fields <= data.keys() <= fields | optional:
        raise PublicError(400, "입력 항목을 확인해 주세요.")
    return data


def create_app(url=None, token=None, transport=None):
    @asynccontextmanager
    async def lifespan(app):
        gateway = Gateway(os.environ.get("BACKEND_API_URL", "") if url is None else url,
                          os.environ.get("BACKEND_SHARED_TOKEN", "") if token is None else token, transport)
        app.state.gateway = gateway
        try:
            yield
        finally:
            await gateway.client.aclose()

    app = FastAPI(title="Fincode LAB 1.0", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.exception_handler(PublicError)
    async def error(request, exc):
        return JSONResponse({"error": {"message": exc.message}}, status_code=exc.status)

    @app.middleware("http")
    async def protect(request, call_next):
        result = await call_next(request)
        result.headers["X-Content-Type-Options"] = "nosniff"
        result.headers["Referrer-Policy"] = "same-origin"
        result.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            result.headers["Cache-Control"] = "no-store"
        return result

    @app.get("/api/ready")
    async def ready():
        available = (await app.state.gateway.read("/api/ready")).get("ready") is True
        return JSONResponse({"ready": available}, status_code=200 if available else 503)

    @app.get("/api/public/demo")
    async def demo():
        gateway = app.state.gateway
        models, samples = await asyncio.gather(gateway.models(), gateway.samples())
        return {"models": models, "emails": [project(item, "id title body reference") for item in samples.values()],
                "defenseModes": MODES}

    @app.post("/api/public/summarize")
    async def summarize(request: Request):
        data = await payload(request, {"model", "email"})
        email = data["email"]
        if not isinstance(email, dict) or set(email) != {"subject", "body"}:
            raise PublicError(400, "이메일 제목과 본문을 입력해 주세요.")
        subject, body = email["subject"], email["body"]
        if not isinstance(subject, str) or len(subject) > 200 or not isinstance(body, str) or not body.strip() or len(body) > 5000:
            raise PublicError(400, "제목은 200자, 본문은 1~5,000자로 입력해 주세요.")
        try:
            subject.encode("utf-8")
            body.encode("utf-8")
        except UnicodeEncodeError:
            raise PublicError(400, "올바른 문자로 입력해 주세요.") from None
        if data["model"] not in await app.state.gateway.models():
            raise PublicError(400, "목록에 있는 모델을 선택해 주세요.")
        return await app.state.gateway.summarize(data["model"], subject, body, "both")

    @app.post("/api/public/evaluate")
    async def evaluate(request: Request):
        data = await payload(request, {"model", "emailId", "defense"})
        gateway = app.state.gateway
        if data["model"] not in await gateway.models() or data["defense"] not in MODES:
            raise PublicError(400, "목록에서 모델과 방어 설정을 선택해 주세요.")
        if not isinstance(data["emailId"], str) or data["emailId"] not in (samples := await gateway.samples()):
            raise PublicError(400, "검토된 공개 평가 이메일을 선택해 주세요.")
        sample = samples[data["emailId"]]
        result = await gateway.summarize(data["model"], sample["title"], sample["body"], data["defense"])
        grade = evaluate_summary(sample["reference"], sample["requiredFacts"], result["summary"],
                                 source_body=sample["body"], fact_evidence=sample["factEvidence"])
        return {**result, "reference": sample["reference"], "quality": {
            "referenceSimilarity": grade["score"], "confirmedFactRate": grade["details"]["factCoverage"]}}

    @app.get("/api/public/overview")
    async def overview():
        gateway = app.state.gateway
        async def load():
            return overview_view(await gateway.read("/api/dashboard"))
        return await gateway.cached("overview", load)

    @app.get("/api/public/credits")
    async def credits():
        gateway = app.state.gateway
        async def load():
            raw = await gateway.read("/api/credits")
            return {"remaining": raw["balance"].get("remaining") if raw.get("available") else None}
        return await gateway.cached("credits", load)

    @app.get("/api/public/comparison")
    async def comparison(purpose: str = "attack", defense: str = "off", vector: str = "all"):
        if (purpose not in ("attack", "normal", "performance") or defense not in MODES
                or vector not in ("all", "direct", "indirect") or (purpose != "attack" and vector != "all")):
            raise PublicError(400, "실험 종류와 방어 설정 및 경로를 확인해 주세요.")
        gateway = app.state.gateway
        async def load():
            return comparison_view(await gateway.read("/api/results/models", {"purpose":purpose, "defense":defense, "vector":vector}))
        return await gateway.cached(("comparison", purpose, defense, vector), load)

    @app.get("/api/public/comparison-records")
    async def records(kind: str = "normal", page: int = 1):
        try:
            return await comparison_records(app.state.gateway.read, kind=kind, page=page, catalog_models=MODELS)
        except ValueError:
            raise PublicError(400, "비교할 기록의 종류와 페이지를 확인해 주세요.") from None

    @app.post("/api/public/compare")
    async def compare(request: Request):
        data = await payload(request, {"kind", "selections"}, {"vector"})
        try:
            return await compare_saved(app.state.gateway.post, data, catalog_models=MODELS)
        except ValueError:
            raise PublicError(400, "비교할 실험과 조건을 확인해 주세요.") from None

    @app.get("/api/public/history")
    async def history(page: int = 1, model: str = ""):
        try:
            return await public_history(app.state.gateway.read, page=page, model=model, catalog_models=MODELS)
        except ValueError:
            raise PublicError(400, "페이지와 모델을 확인해 주세요.") from None

    @app.get("/style.css")
    async def shared_style():
        return FileResponse(ROOT / "public/style.css")

    @app.get("/dashboard.css")
    async def dashboard_style():
        return FileResponse(ROOT / "public/dashboard.css")

    @app.get("/result-comparison.js")
    async def result_script():
        return FileResponse(ROOT / "public/result-comparison.js", media_type="text/javascript")

    @app.get("/result-comparison.css")
    async def result_style():
        return FileResponse(ROOT / "public/result-comparison.css")

    @app.get("/favicon.svg")
    async def favicon():
        return FileResponse(ROOT / "public/favicon.svg")

    app.mount("/", StaticFiles(directory=ROOT / "release/web", html=True), name="public_dashboard")
    return app


if __name__ == "__main__":
    uvicorn.run(create_app(), host="0.0.0.0", port=server_port(), access_log=False)
