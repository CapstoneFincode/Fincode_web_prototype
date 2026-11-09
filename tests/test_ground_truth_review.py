"""Offline safeguards for scoped approval; no network or model calls."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('review_tool', ROOT / 'scripts/review-ground-truth.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


class GroundTruthReviewTest(unittest.TestCase):
    def setUp(self):
        source = json.loads((ROOT / 'datasets/ground_truth_set/v1/emails.json').read_text())
        maps = json.loads((ROOT / 'docs/verification/2026-11-09-model-evaluation-import.json').read_text())['mappings']
        by_source = {row['sourceId']: row for row in maps}
        rows = []
        for original in source:
            mapping = by_source[original['id']]
            rows.append({**{key: original[key] for key in ('title', 'body', 'reference', 'requiredFacts')},
                'id': mapping['id'], 'dbId': mapping['dbId'], 'dbVersion': mapping['dbVersion'],
                'provenance': 'user', 'reviewStatus': 'pending'})
        self.data = {'normal': rows + [{'id': 'old-sample', 'provenance': 'sample', 'body': 'preserve'}],
                     'attacks': [{'id': 'attack', 'payload': 'preserve'}], 'categories': [{'id': 'cat', 'scope': 'direct'}]}

    def test_only_original_hundred_are_selected(self):
        rows = review.prepare(self.data)
        self.assertEqual(len(rows), 100)
        self.assertEqual(rows[0][0], 'GT-SYN-001')
        self.assertEqual(rows[-1][0], 'GT-SYN-100')
        self.assertNotIn('old-sample', {row['id'] for _, row, _ in rows})

    def test_changed_content_or_server_hash_aborts_preview(self):
        for key, value in [('title', 'changed'), ('body', 'changed'), ('reference', 'changed'),
                           ('requiredFacts', ['changed']), ('reviewContentSha256', '0' * 64), ('provenance', 'sample')]:
            changed = copy.deepcopy(self.data)
            changed['normal'][33][key] = value
            with self.assertRaises(ValueError):
                review.prepare(changed)

    def test_duplicate_or_missing_record_is_not_silently_skipped(self):
        changed = copy.deepcopy(self.data)
        changed['normal'].append(changed['normal'][0])
        with self.assertRaises(ValueError):
            review.prepare(changed)
        changed = copy.deepcopy(self.data)
        changed['normal'].pop(0)
        with self.assertRaises(KeyError):
            review.prepare(changed)

    def test_status_only_snapshot_detects_other_changes(self):
        ids = {row['id'] for _, row, _ in review.prepare(self.data)}
        before = review.protected_snapshot(self.data, ids)
        changed = copy.deepcopy(self.data)
        changed['normal'][0]['reviewStatus'] = 'reviewed'
        self.assertEqual(before, review.protected_snapshot(changed, ids))
        changed['normal'][0]['dbVersion'] += 1
        self.assertNotEqual(before, review.protected_snapshot(changed, ids))
        self.assertEqual(review.protected_snapshot(self.data, ids, True), review.protected_snapshot(changed, ids, True))
        changed['attacks'][0]['payload'] = 'unexpected'
        self.assertNotEqual(review.protected_snapshot(self.data, ids, True), review.protected_snapshot(changed, ids, True))

    def test_write_allowlist_rejects_model_and_other_data_endpoints(self):
        for method, path in [('POST', '/api/summarize'), ('POST', '/api/experiments'),
                              ('DELETE', '/api/data/normal/x'), ('PUT', '/api/data/attacks/x'),
                              ('POST', 'https://elsewhere.example/api/data/normal/review')]:
            with self.assertRaises(ValueError):
                review.write_request(None, method, path, {})

    def test_non_successful_write_is_never_replayed(self):
        class Response:
            status = 503
            def read(self, _):
                return b'{}'
        class Connection:
            calls = 0
            def request(self, *args, **kwargs):
                self.calls += 1
            def getresponse(self):
                return Response()
        class Client:
            _token = 'offline-test-only'
            _connection = Connection()
        client = Client()
        with self.assertRaisesRegex(RuntimeError, '503'):
            review.write_request(client, 'POST', '/api/data/normal/review', {'items': []})
        self.assertEqual(client._connection.calls, 1)


if __name__ == '__main__':
    unittest.main()
