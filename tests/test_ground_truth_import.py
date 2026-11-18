"""Offline import safety checks: no network, credentials, DB, or model calls."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ground_truth_import', ROOT / 'scripts/import-ground-truth.py')
IMPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IMPORT)


def candidate(number):
    return {'id': f'GT-SYN-{number:03d}', 'title': f'Title {number}', 'body': f'Email body {number}',
            'reference': f'Reference {number}', 'requiredFacts': [f'Fact {number}'],
            'reviewStatus': 'pending', 'referenceReviewStatus': 'draft',
            'expectedLabel': 'normal', 'provenance': 'synthetic'}


def saved(row, number=1):
    return {**{key: row[key] for key in ('title', 'body', 'reference', 'requiredFacts', 'reviewStatus')},
            'id': f'GT-server-{number}', 'dbId': f'00000000-0000-4000-8000-{number:012d}',
            'dbVersion': 1, 'provenance': 'user'}


def report():
    return {'created': 0, 'mappings': []}


class FakeClient:
    def __init__(self, normal=None, uncertain=False):
        self.data = {'normal': normal or [], 'attacks': [{'id': 'ATK-existing', 'payload': 'kept'}],
                     'categories': [{'id': 'category-existing', 'scope': 'both'}]}
        self.calls, self.uncertain = [], uncertain
        self.experiment = '00000000-0000-4000-8000-000000009999'

    def request(self, method, path, payload=None):
        self.calls.append((method, path))
        if method == 'GET' and path == '/api/data':
            return copy.deepcopy(self.data)
        if method == 'GET' and path == '/api/experiments':
            return {'experiments': [{'id': self.experiment}]}
        if method == 'POST' and path == '/api/data/normal':
            self.data['normal'].append(saved(payload, len(self.data['normal']) + 1))
            if self.uncertain:
                self.uncertain = False
                raise IMPORT.ImportFailure('HTTP_TRANSPORT_FAILURE')
            return copy.deepcopy(self.data)
        raise AssertionError('Unexpected endpoint')


class CandidateValidationTest(unittest.TestCase):
    def write_dataset(self, folder, rows):
        path = Path(folder) / 'emails.json'
        path.write_text(json.dumps(rows), encoding='utf-8')
        (path.parent / 'manifest.json').write_text(json.dumps({
            'recordCount': 100, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}))
        return path

    def test_exact_release_hash_and_unreviewed_state_required(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = [candidate(i) for i in range(1, 101)]
            path = self.write_dataset(folder, rows)
            self.assertEqual(len(IMPORT.load_candidates(path)[0]), 100)
            path.write_text(path.read_text() + ' ')
            with self.assertRaisesRegex(IMPORT.ImportFailure, 'MANIFEST_MISMATCH'):
                IMPORT.load_candidates(path)
            rows[-1]['referenceReviewStatus'] = 'reviewed'
            path = self.write_dataset(folder, rows)
            with self.assertRaisesRegex(IMPORT.ImportFailure, 'NOT_UNREVIEWED'):
                IMPORT.load_candidates(path)

    def test_duplicate_ids_and_content_rejected(self):
        for change, code in [('id', 'DUPLICATE_ID'), ('title', 'DUPLICATE_CONTENT')]:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                rows = [candidate(i) for i in range(1, 101)]
                rows[-1][change] = rows[0][change]
                with self.assertRaisesRegex(IMPORT.ImportFailure, code):
                    IMPORT.load_candidates(self.write_dataset(folder, rows))


class ImportWorkflowTest(unittest.TestCase):
    def test_dry_run_does_not_post(self):
        client, result = FakeClient(), report()
        IMPORT.run_import(client, [candidate(1), candidate(2)], False, result)
        self.assertEqual(result['status'], 'dry-run-ready')
        self.assertEqual(result['plannedMissing'], 2)
        self.assertTrue(all(method == 'GET' for method, _ in client.calls))

    def test_whole_batch_conflict_preflight_before_any_post(self):
        conflicting = saved(candidate(2))
        conflicting['reference'] = 'Changed'
        client = FakeClient([conflicting])
        with self.assertRaisesRegex(IMPORT.ImportFailure, 'CONTENT_CONFLICT'):
            IMPORT.run_import(client, [candidate(1), candidate(2)], True, report())
        self.assertTrue(all(method == 'GET' for method, _ in client.calls))

    def test_ambiguous_existing_data_refuses_import(self):
        client = FakeClient([saved(candidate(1), 1), saved(candidate(1), 2)])
        with self.assertRaisesRegex(IMPORT.ImportFailure, 'AMBIGUOUS'):
            IMPORT.run_import(client, [candidate(1)], True, report())

    def test_apply_rerun_is_additive_and_preserves_other_data(self):
        rows, client, first = [candidate(1), candidate(2)], FakeClient(), report()
        original = copy.deepcopy(client.data)
        IMPORT.run_import(client, rows, True, first)
        self.assertEqual(first['created'], 2)
        self.assertEqual(first['status'], 'verified')
        self.assertEqual(client.data['attacks'], original['attacks'])
        self.assertEqual(client.data['categories'], original['categories'])
        second, count = report(), len(client.calls)
        IMPORT.run_import(client, rows, True, second)
        self.assertEqual(second['created'], 0)
        self.assertEqual(len(second['mappings']), 2)
        self.assertTrue(all(method == 'GET' for method, _ in client.calls[count:]))

    def test_uncertain_post_not_retried_and_rerun_reconciles_saved_row(self):
        rows, client, first = [candidate(1), candidate(2)], FakeClient(uncertain=True), report()
        with self.assertRaisesRegex(IMPORT.ImportFailure, 'TRANSPORT'):
            IMPORT.run_import(client, rows, True, first)
        self.assertEqual(first['uncertainSourceId'], 'GT-SYN-001')
        self.assertEqual(sum(method == 'POST' for method, _ in client.calls), 1)
        second = report()
        IMPORT.run_import(client, rows, True, second)
        self.assertEqual(second['created'], 1)
        self.assertEqual(len(client.data['normal']), 2)

    def test_concurrent_additions_allowed_but_modification_detected(self):
        before = IMPORT.dataset_snapshot(FakeClient().data)
        data = FakeClient().data
        data['attacks'].append({'id': 'new-scenario', 'payload': 'new'})
        IMPORT.preserve(before, IMPORT.dataset_snapshot(data))
        data['attacks'][0]['payload'] = 'changed'
        with self.assertRaisesRegex(IMPORT.ImportFailure, 'ATTACKS_CHANGED'):
            IMPORT.preserve(before, IMPORT.dataset_snapshot(data))

    def test_list_rollover_checks_missing_experiment_by_detail(self):
        class RolloverClient(FakeClient):
            reads = 0
            def request(self, method, path, payload=None):
                if path == '/api/experiments':
                    self.reads += 1
                    return {'experiments': [{'id': self.experiment}]} if self.reads == 1 else {'experiments': []}
                if path == '/api/experiments/' + self.experiment:
                    return {'experiment': {'id': self.experiment}}
                return super().request(method, path, payload)
        client, result = RolloverClient(), report()
        IMPORT.run_import(client, [candidate(1)], True, result)
        self.assertEqual(result['after']['olderExperimentIdsVerifiedByDetail'], [client.experiment])

    def test_cli_saves_partial_report_without_token_or_payload(self):
        token, client = 'test-secret-never-print', FakeClient(uncertain=True)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'report.json'
            stdout = io.StringIO()
            with (patch.object(IMPORT, 'load_candidates', return_value=([candidate(1)], 'hash')),
                    patch.object(IMPORT, 'Client', return_value=client),
                    patch.object(IMPORT.sys, 'stdin', io.StringIO(token + '\n')),
                    contextlib.redirect_stdout(stdout)):
                code = IMPORT.main(['unused.json', '--site', 'https://example.test', '--report', str(output), '--apply'])
            self.assertEqual(code, 1)
            content = output.read_text() + stdout.getvalue()
            self.assertNotIn(token, content)
            self.assertNotIn(candidate(1)['body'], content)
            result = json.loads(output.read_text())
            self.assertEqual(result['uncertainSourceId'], candidate(1)['id'])
            self.assertEqual(result['status'], 'stopped')


class TransportSafetyTest(unittest.TestCase):
    def test_no_insecure_origin_or_credentials_in_url(self):
        for site in ['http://example.test', 'https://token@example.test', 'https://example.test/api',
                     'https://example.test?token=secret', 'https://example.test/#fragment']:
            with self.subTest(site=site), self.assertRaises(IMPORT.ImportFailure):
                IMPORT.validate_site(site)

    def test_non_import_endpoints_refused(self):
        client = IMPORT.Client('https://example.test', 'test-token')
        for method, path in [('POST', '/api/summarize'), ('POST', '/api/experiments'),
                             ('GET', '//other.test'), ('DELETE', '/api/data/normal/id')]:
            with self.subTest(path=path), self.assertRaisesRegex(IMPORT.ImportFailure, 'NOT_PERMITTED'):
                client.request(method, path)

    def test_direct_https_connection_reused_with_exact_paths(self):
        connection = Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = b'{"ok":true}'
        with (patch.object(IMPORT, 'getproxies', return_value={}),
              patch.object(IMPORT, 'proxy_bypass', return_value=False),
              patch.object(IMPORT.http.client, 'HTTPSConnection', return_value=connection) as factory):
            client = IMPORT.Client('https://example.test', 'test-token')
            self.assertEqual(client.request('GET', '/api/data'), {'ok': True})
            client.request('GET', '/api/experiments')
        factory.assert_called_once_with('example.test', 443, timeout=60)
        self.assertEqual(connection.request.call_count, 2)
        self.assertEqual(connection.request.call_args_list[0].args, ('GET', '/api/data'))
        self.assertEqual(connection.request.call_args.kwargs['headers']['OAI-Sites-Authorization'], 'Bearer test-token')
        connection.set_tunnel.assert_not_called()

    def test_http_proxy_tunnels_only_to_fixed_https_target(self):
        connection = Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = b'{}'
        with (patch.object(IMPORT, 'getproxies', return_value={'https': 'http://proxy.test:8080'}),
              patch.object(IMPORT, 'proxy_bypass', return_value=False),
              patch.object(IMPORT.http.client, 'HTTPSConnection', return_value=connection) as factory):
            IMPORT.Client('https://example.test', 'test-token').request('GET', '/api/data')
        factory.assert_called_once_with('proxy.test', 8080, timeout=60)
        connection.set_tunnel.assert_called_once_with('example.test', 443)

    def test_redirect_never_followed_and_connection_discarded(self):
        client, connection = IMPORT.Client('https://example.test', 'test-token'), Mock()
        client._connection = connection
        connection.getresponse.return_value.status = 302
        with self.assertRaisesRegex(IMPORT.ImportFailure, 'HTTP_REDIRECT_REFUSED'):
            client.request('GET', '/api/data')
        self.assertEqual(connection.request.call_count, 1)
        connection.getresponse.return_value.read.assert_not_called()
        connection.close.assert_called_once()
        self.assertIsNone(client._connection)

    def test_disconnected_post_never_retried_or_exception_text_exposed(self):
        client, connection = IMPORT.Client('https://example.test', 'test-token'), Mock()
        client._connection = connection
        connection.getresponse.side_effect = IMPORT.http.client.RemoteDisconnected('private upstream details')
        with self.assertRaises(IMPORT.ImportFailure) as caught:
            client.request('POST', '/api/data/normal', {'title': 'local test'})
        self.assertEqual(str(caught.exception), 'HTTP_TRANSPORT_FAILURE')
        self.assertEqual(connection.request.call_count, 1)
        connection.close.assert_called_once()
        self.assertIsNone(client._connection)

    def test_response_size_is_bounded(self):
        client, connection = IMPORT.Client('https://example.test', 'test-token'), Mock()
        client._connection = connection
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = b'x' * (10 * 1024 * 1024 + 1)
        with self.assertRaisesRegex(IMPORT.ImportFailure, 'RESPONSE_TOO_LARGE'):
            client.request('GET', '/api/data')
        connection.getresponse.return_value.read.assert_called_once_with(10 * 1024 * 1024 + 1)
        connection.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
