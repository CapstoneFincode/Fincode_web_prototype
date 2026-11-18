import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from release.history import public_history


def summary(models):
    return {"name": "private-name", "id": "private-id", "notes": "private-notes",
            "models": models, "purpose": "performance", "status": "completed",
            "created_at": "2026-11-18T12:00:00+09:00", "defense_enabled": False,
            "finished": 2, "total": 2}


class PublicHistoryTests(IsolatedAsyncioTestCase):
    async def test_filtered_parent_keeps_approved_participants_and_only_summary_fields(self):
        get = AsyncMock(return_value={"page": 2, "pageSize": 5, "total": 6, "totalPages": 2,
            "experiments": [summary(["gpt-5.1", "private-alias", "new-catalog-model"])]})
        result = await public_history(get, page=2, model="gpt-5.1", catalog_models=["new-catalog-model"])
        get.assert_awaited_once_with("/api/experiments", {"page": 2, "pageSize": 5, "model": "gpt-5.1"})
        self.assertNotIn("private-", json.dumps(result))
        self.assertEqual(result["rows"][0], {
            "models": ["gpt-5.1", "new-catalog-model"], "purpose": "performance",
            "status": "completed", "createdAt": "2026-11-18T03:00:00+00:00",
            "defense": False, "finished": 2, "total": 2})
        self.assertEqual((result["page"], result["total"], result["totalPages"]), (2, 6, 2))
        self.assertIn("new-catalog-model", result["models"])

    async def test_empty_history_and_invalid_parameters(self):
        get = AsyncMock(return_value={"page": 1, "pageSize": 5, "total": 0, "totalPages": 1, "experiments": []})
        for params in ({"model":"private-alias"}, {"page":0}, {"page":True}):
            with self.assertRaises(ValueError):
                await public_history(get, **params)
        get.assert_not_awaited()
        self.assertEqual((await public_history(get))["rows"], [])
        get.assert_awaited_once_with("/api/experiments", {"page": 1, "pageSize": 5})
