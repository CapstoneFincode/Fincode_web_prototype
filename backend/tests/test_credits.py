"""Credit balance contracts use a mocked gateway and never spend credits."""

import asyncio
import unittest
from unittest.mock import patch

import httpx

from backend.app import APIError, GATEWAY_BASE, create_app
from backend.credits import CreditBalance


def balance(**total):
    return {
        "object": "credit_balance",
        "total": {"quota": 1000.5, "used": 40.25, "remaining": 960.25, **total},
        "monthly_allocated": {"renewal_date": "2026-12-01T00:00:00Z"},
        "internal_account": "never-expose-upstream-fields",
    }


class CreditAPITest(unittest.IsolatedAsyncioTestCase):
    async def request(self, handler=None, key="test-key-not-real", headers=None):
        self.calls = []

        def record(request):
            self.calls.append(request)
            return handler(request) if handler else httpx.Response(200, json=balance())

        app = create_app(database_url="", api_key=key, upstream_transport=httpx.MockTransport(record))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            return await client.get("/api/credits", headers=headers)

    async def test_credits_are_account_balance_not_tokens_and_get_is_read_only(self):
        result = await self.request()
        self.assertEqual(result.status_code, 200)
        snapshot = result.json()
        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["unit"], "credits")
        self.assertEqual(snapshot["provider"], "baze")
        self.assertEqual(snapshot["balance"], {"remaining": 960.25, "used": 40.25, "quota": 1000.5})
        self.assertEqual(snapshot["renewalDate"], "2026-12-01T00:00:00Z")
        self.assertTrue(snapshot["checkedAt"].endswith("Z"))
        self.assertFalse(snapshot["cached"])
        self.assertIsNone(snapshot["error"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0].method, "GET")
        self.assertEqual(str(self.calls[0].url), f"{GATEWAY_BASE}/credits/")
        self.assertEqual(self.calls[0].headers["authorization"], "Bearer test-key-not-real")
        self.assertEqual(self.calls[0].content, b"")
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertEqual(result.headers["x-content-type-options"], "nosniff")
        self.assertNotIn("test-key-not-real", result.text)
        self.assertNotIn("internal_account", result.text)

    async def test_zero_and_negative_balance_are_not_unavailable(self):
        for remaining in [0, -0.25]:
            with self.subTest(remaining=remaining):
                result = await self.request(lambda _: httpx.Response(200, json=balance(remaining=remaining)))
                self.assertTrue(result.json()["available"])
                self.assertEqual(result.json()["balance"]["remaining"], remaining)

    async def test_missing_key_does_not_call_gateway_or_report_zero(self):
        result = await self.request(key=" ")
        self.assertFalse(result.json()["available"])
        self.assertEqual(result.json()["error"]["code"], "API_KEY_MISSING")
        self.assertEqual(result.json()["balance"], {"remaining": None, "used": None, "quota": None})
        self.assertEqual(self.calls, [])

    async def test_upstream_failures_have_safe_messages_and_no_assumed_balance(self):
        for status, code in [(401, "GATEWAY_AUTH_ERROR"), (404, "GATEWAY_REQUEST_REJECTED"), (429, "GATEWAY_RATE_LIMIT"), (500, "GATEWAY_UNAVAILABLE")]:
            with self.subTest(status=status):
                result = await self.request(lambda _, status=status: httpx.Response(status, json={"error": "test-key-not-real private account"}))
                data = result.json()
                self.assertFalse(data["available"])
                self.assertEqual(data["error"]["code"], code)
                self.assertEqual(data["balance"], {"remaining": None, "used": None, "quota": None})
                self.assertNotIn("test-key-not-real", result.text)
                self.assertNotIn("private account", result.text)

    async def test_timeout_has_credit_specific_message(self):
        def timeout(request):
            raise httpx.ReadTimeout("test-key-not-real", request=request)
        result = await self.request(timeout)
        self.assertEqual(result.json()["error"]["code"], "GATEWAY_TIMEOUT")
        self.assertIn("크레딧", result.json()["error"]["message"])
        self.assertNotIn("test-key-not-real", result.text)

    async def test_shared_token_still_protects_balance(self):
        with patch.dict("os.environ", {"BACKEND_SHARED_TOKEN": "backend-test-secret"}):
            denied = await self.request()
            self.assertEqual(denied.status_code, 401)
            self.assertEqual(self.calls, [])
            allowed = await self.request(headers={"X-Fincode-Backend-Token": "backend-test-secret"})
            self.assertEqual(allowed.status_code, 200)
            self.assertTrue(allowed.json()["available"])


class CreditCacheTest(unittest.IsolatedAsyncioTestCase):
    def make_service(self, data=None):
        self.now = 0.0
        self.key = "test-key-not-real"
        self.calls = 0
        self.data = balance() if data is None else data
        self.error = None

        async def gateway(*args, **kwargs):
            self.calls += 1
            await asyncio.sleep(0)
            if self.error:
                raise self.error
            return self.data, 1

        return CreditBalance(gateway, lambda: self.key, APIError, clock=lambda: self.now)

    async def test_concurrent_reads_coalesce_and_cached_timestamp_does_not_change(self):
        service = self.make_service()
        results = await asyncio.gather(*(service.get() for _ in range(12)))
        self.assertEqual(self.calls, 1)
        self.assertEqual(sum(not result["cached"] for result in results), 1)
        self.assertEqual(len({result["checkedAt"] for result in results}), 1)
        self.now = 59.9
        self.assertTrue((await service.get())["cached"])
        self.now = 60
        self.assertFalse((await service.get())["cached"])
        self.assertEqual(self.calls, 2)

    async def test_failed_refresh_clears_old_balance_and_is_also_cached(self):
        service = self.make_service()
        self.assertTrue((await service.get())["available"])
        self.now = 60
        self.error = APIError(502, "GATEWAY_UNAVAILABLE", "private upstream content")
        failed = await service.get()
        self.assertFalse(failed["available"])
        self.assertIsNone(failed["balance"]["remaining"])
        self.assertIsNone(failed["renewalDate"])
        self.assertNotIn("private upstream content", failed["error"]["message"])
        self.assertTrue((await service.get())["cached"])
        self.assertEqual(self.calls, 2)

    async def test_key_rotation_does_not_reuse_another_accounts_balance(self):
        service = self.make_service()
        await service.get()
        self.key = "rotated-test-key"
        self.data = balance(remaining=2.5)
        snapshot = await service.get()
        self.assertFalse(snapshot["cached"])
        self.assertEqual(snapshot["balance"]["remaining"], 2.5)
        self.assertEqual(self.calls, 2)

    async def test_invalid_balance_is_not_inferred_from_quota_or_used(self):
        values = [None, True, "12", float("nan"), float("inf"), 10 ** 400, {}, []]
        for value in values:
            with self.subTest(value=str(value)[:25]):
                snapshot = await self.make_service(balance(remaining=value)).get()
                self.assertFalse(snapshot["available"])
                self.assertEqual(snapshot["error"]["code"], "CREDITS_INVALID_RESPONSE")
                self.assertEqual(snapshot["balance"], {"remaining": None, "used": None, "quota": None})
        for data in [{}, {"object": "wrong", "total": {"remaining": 4}}]:
            self.assertFalse((await self.make_service(data).get())["available"])

    async def test_optional_fields_are_null_when_missing_or_invalid(self):
        data = {"object": "credit_balance", "total": {"remaining": 12, "used": True, "quota": -3}, "monthly_allocated": {"renewal_date": "not-an-iso-date"}}
        snapshot = await self.make_service(data).get()
        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["balance"], {"remaining": 12, "used": None, "quota": None})
        self.assertIsNone(snapshot["renewalDate"])


if __name__ == "__main__":
    unittest.main()
