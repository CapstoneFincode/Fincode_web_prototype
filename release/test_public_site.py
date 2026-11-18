"""Offline release-boundary tests. Model calls and the private backend are stubbed."""
import json
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from release.public_site import PUBLIC_SAMPLES, Gateway, create_app


class PublicSiteTests(unittest.TestCase):
    def setUp(self):
        self.upstream = []
        self.target = "gemini-3.5-flash-lite"
        self.row = PUBLIC_SAMPLES[0]
        self.normal = {
            **self.row, "dbId": "db-id", "dbVersion": 2, "reviewStatus": "reviewed",
            "referenceReviewStatus": "reviewed", "groundTruthId": "reference-id",
        }

        def respond(req):
            self.assertEqual(req.headers["X-Fincode-Backend-Token"], "test-token")
            self.upstream.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
            if req.url.path == "/api/models":
                result = {"models": [{"id": self.target}, {"id": "unlisted"}]}
            elif req.url.path == "/api/ready":
                result = {"ready": True}
            elif req.url.path == "/api/performance/dataset":
                result = {"dataset": {"items": [{
                    "sourceId": self.row["id"], "id": "db-id", "dbVersion": 2, "groundTruthId": "reference-id",
                    "reviewStatus": "reviewed", "referenceReviewStatus": "reviewed",
                }]}}
            elif req.url.path == "/api/data":
                result = {"normal": [self.normal], "attacks": [{"payload": "private text"}]}
            elif req.url.path == "/api/summarize":
                result = {"model": self.target, "summary": self.row["reference"], "latencyMs": 60,
                          "usage": {"inputTokens": 4, "totalTokens": 77, "debug": "private text"},
                          "defenseResult": "private text", "database": {"saved": False}}
            elif req.url.path == "/api/dashboard":
                result = {"datasets": {"groundTruth": {"total": 100, "ready": 100}, "attacks": 11},
                          "experiments": {"recent": [{"name": "private text"}], "total": 56}, "performance": {}}
            elif req.url.path == "/api/credits":
                result = {"available": True, "balance": {"remaining": 42, "quota": "private text"}}
            elif req.url.path == "/api/results/models":
                result = {"purpose": "attack", "defense": "off", "vector": "all", "models": [
                    {"model": self.target, "total": 13, "tokens": {"totalMean": 50, "raw": "private text"},
                     "attack": {"successes": 8, "failures": 2, "judged": 10, "asr": 80}, "snapshot": "private text"},
                    {"model": "private text", "tokens": {}}]}
            else:
                raise AssertionError("Unexpected private endpoint")
            return httpx.Response(200, json=result)

        self.app = create_app("https://lab.example", "test-token", httpx.MockTransport(respond))
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_public_catalog_only_release_content_approved_by_db(self):
        result = self.client.get("/api/public/demo").json()
        self.assertEqual(result["models"], [self.target])
        self.assertEqual(result["emails"], [{k: self.row[k] for k in ("id", "title", "body", "reference")}])
        self.assertNotIn("private text", json.dumps(result))
        self.assertEqual(len(self.upstream), 3)
        self.client.get("/api/public/demo")
        self.assertEqual(len(self.upstream), 3)

    def test_edited_or_unreviewed_db_content_is_never_published(self):
        self.normal["reference"] = "private text"
        self.assertEqual(self.client.get("/api/public/demo").json()["emails"], [])

    def test_freeform_summary_always_uses_both_without_untrusted_options(self):
        body = {"model": self.target, "email": {"subject": "회의", "body": "모임 시각은 오후 2시입니다."}}
        res = self.client.post("/api/public/summarize", json=body)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["defense"], "both")
        self.assertNotIn("private text", res.text)
        self.assertEqual(self.upstream[-1][2], {**body, "defense": "both"})
        for extra in ({"defense": "off"}, {"url": "https://elsewhere"}, {"attachments": ["id"]}):
            self.assertEqual(self.client.post("/api/public/summarize", json={**body, **extra}).status_code, 400)
        self.assertEqual(self.client.post("/api/public/summarize", json={**body, "model": "other"}).status_code, 400)
        self.assertEqual(self.client.post("/api/public/summarize", json=body, headers={"Origin": "https://elsewhere.example"}).status_code, 403)
        self.assertEqual(len([r for r in self.upstream if r[0] == "POST"]), 1)

    def test_evaluation_uses_fixed_email_with_scores_and_omits_reference_from_model_input(self):
        res = self.client.post("/api/public/evaluate", json={"model": self.target, "emailId": self.row["id"], "defense": "off"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["quality"]["referenceSimilarity"], 1)
        self.assertEqual(self.upstream[-1][2], {"model": self.target, "email": {"subject": self.row["title"], "body": self.row["body"]}, "defense": "off"})
        self.assertNotIn(self.row["reference"], str(self.upstream[-1][2]))
        self.assertEqual(self.client.post("/api/public/evaluate", json={"model": self.target, "emailId": {}, "defense": "off"}).status_code, 400)

    def test_only_public_aggregate_fields_and_route_combinations(self):
        for route in ("overview", "comparison", "credits"):
            resp = self.client.get("/api/public/" + route)
            self.assertEqual(resp.status_code, 200)
            self.assertNotIn("private text", resp.text)
        self.assertEqual(len(self.client.get("/api/public/comparison").json()["models"]), 1)
        self.assertEqual(self.client.get("/api/public/comparison?purpose=normal&vector=direct").status_code, 400)
        self.assertEqual(self.client.get("/api/public/credits").json(), {"remaining": 42})

    def test_public_capacity_limit_rejects_before_model_call(self):
        with patch("release.public_site.time.monotonic", return_value=100):
            self.app.state.gateway.recent_calls.extend([99] * 120)
            resp = self.client.post("/api/public/summarize", json={"model": self.target, "email": {"subject": "", "body": "모임 안내"}})
        self.assertEqual(resp.status_code, 429)
        self.assertFalse(any(row[0] == "POST" for row in self.upstream))

    def test_private_surface_is_unavailable_and_body_size_bounded(self):
        for path in ("/api/data", "/api/experiments", "/api/summarize", "/api/performance", "/api/attachments",
                     "/data.js", "/app.js", "/openapi.json"):
            self.assertIn(self.client.get(path).status_code, (404, 405))
            self.assertIn(self.client.post(path, json={}).status_code, (404, 405))
        self.assertEqual(self.client.post("/api/public/summarize", json={"a": "x" * 60_000}).status_code, 413)
        self.assertEqual(self.upstream, [])

    def test_rejects_unsafe_backend_origins(self):
        for url in ("http://public.test", "https://user:secret@lab.example", "https://lab.example/subpath"):
            with self.assertRaises(ValueError):
                Gateway(url, "test-token", None)
