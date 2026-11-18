import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
from uuid import uuid4

from release.matched import METRIC_COUNTS, compare_saved, comparison_records


def choice(mode):
    return {"experimentId": str(uuid4()), "model": "gpt-5.1", "defense": mode}


def fake_metrics(**overrides):
    return {**dict.fromkeys(METRIC_COUNTS, 0), **overrides, "private": "private-test"}


class PublicComparisonTests(IsolatedAsyncioTestCase):
    async def test_catalog_skips_arbitrary_strings(self):
        a = {**choice("off"), "name": "private-test", "runCount": 8, "status": "completed",
             "createdAt": "2026-11-18T12:00:00Z", "vectorCounts": {"direct": 8, "indirect": 0, "unknown": 0}}
        get = AsyncMock(return_value={"records": [a, {**a, "model": "private-test"}], "page": 1, "totalPages": 1, "total": 2})
        result = await comparison_records(get, kind="attack")
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["vectorCounts"]["direct"], 8)
        self.assertNotIn("private-test", json.dumps(result))
        get.assert_awaited_once_with("/api/results/comparison-records", {"kind": "attack", "page": 1, "pageSize": 50})

    async def test_same_email_cohort_forwards_real_counts_and_missing_rates(self):
        choices = [choice("off"), choice("filter")]
        post = AsyncMock(return_value={"kind": "normal", "comparable": True, "commonCases": 3, "commonRuns": 4,
            "groups": [{**item, "name": "private-test", "matchedRuns": 4, "excludedRuns": 1,
                        "selectedRuns": 5, "metrics": fake_metrics(meanRougeL=.75)} for item in choices],
            "warnings": ["private-test"]})
        result = await compare_saved(post, {"kind": "normal", "selections": choices})
        self.assertEqual((result["commonCases"], result["commonRuns"]), (3, 4))
        self.assertEqual(result["groups"][0]["metrics"]["meanRougeL"], .75)
        self.assertIsNone(result["groups"][0]["metrics"]["normalRejectionRate"])
        self.assertNotIn("private-test", json.dumps(result))
        self.assertNotIn("experimentId", json.dumps(result))
        post.assert_awaited_once_with("/api/results/compare", {"kind": "normal", "selections": choices})

    async def test_attack_asr_zero_and_missing_stay_distinct(self):
        choices = [{**choice(mode), "vector": "direct"} for mode in ["off", "filter"]]
        slots = [{"index": 0, "runCount": 1, "scenarioCount": 1, "metrics": fake_metrics(asr=0)},
                 {"index": 1, "runCount": 0, "scenarioCount": 0, "metrics": None}]
        post = AsyncMock(return_value={"kind": "attack", "comparable": True, "groups": [
            {**item, "sourceRuns": 1, "selectedRuns": 1, "scenarioCount": 1, "excludedRuns": 0,
             "metrics": fake_metrics()} for item in choices],
            "categories": [{"id": "instruction-override", "name": "private-test", "groups": slots},
                           {"id": "private-test", "name": "private-test", "groups": slots}]})
        result = await compare_saved(post, {"kind": "attack", "selections": choices})
        self.assertEqual(result["categories"][0]["name"], "지시 덮어쓰기")
        self.assertEqual(result["categories"][0]["groups"][0]["metrics"]["asr"], 0)
        self.assertIsNone(result["categories"][0]["groups"][1]["metrics"])
        self.assertNotIn("private-test", json.dumps(result))
        self.assertIsNone(result["commonCases"])

    async def test_invalid_input_never_reaches_upstream(self):
        post = AsyncMock()
        choices = [choice("off"), choice("off")]
        for body in [{"kind": "normal", "selections": choices},
                     {"kind": "attack", "selections": [choice("off"), {**choice("both"), "model": "private"}]},
                     {"kind": "attack", "selections": [choice("off"), {**choice("both"), "text": "arbitrary"}]}]:
            with self.assertRaises(ValueError):
                await compare_saved(post, body)
        post.assert_not_awaited()
