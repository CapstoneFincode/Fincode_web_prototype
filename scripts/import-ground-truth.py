#!/usr/bin/env python3
"""Add the reviewed-format synthetic candidate file through the existing API.

Dry-run is the default. --apply adds missing rows only; it never updates or
deletes rows, starts experiments, or calls models. Run only one importer at a
time: the existing POST endpoint has no server-side idempotency key. If a POST
has an uncertain outcome, stop and rerun to reconcile against live data.

The API exposes the normal-label review state, but not ground-truth review
state or raw DB provenance. Its current backend creates these as draft and
synthetic; this script cannot independently verify those two DB-only fields.
"""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import http.client
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass


class ImportFailure(Exception):
    """Only fixed codes and source IDs may reach the report, never HTTP bodies."""

    def __init__(self, code, source_id=None):
        super().__init__(code)
        self.code, self.source_id = code, source_id


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def load_candidates(path):
    raw = path.read_bytes()
    manifest = json.loads((path.parent / 'manifest.json').read_text(encoding='utf-8'))
    sha = hashlib.sha256(raw).hexdigest()
    if manifest.get('sha256') != sha or manifest.get('recordCount') != 100:
        raise ImportFailure('DATASET_MANIFEST_MISMATCH')
    rows = json.loads(raw)
    if not isinstance(rows, list) or len(rows) != 100:
        raise ImportFailure('DATASET_EXPECTED_100')
    ids, titles, bodies = set(), set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise ImportFailure('DATASET_INVALID_ROW')
        rid = row.get('id')
        if not isinstance(rid, str) or not re.fullmatch(r'GT-SYN-\d{3}', rid):
            raise ImportFailure('DATASET_INVALID_ID')
        if rid in ids:
            raise ImportFailure('DATASET_DUPLICATE_ID', rid)
        ids.add(rid)
        expected = {'expectedLabel': 'normal', 'reviewStatus': 'pending',
                    'referenceReviewStatus': 'draft', 'provenance': 'synthetic'}
        if any(row.get(k) != v for k, v in expected.items()):
            raise ImportFailure('DATASET_NOT_UNREVIEWED_SYNTHETIC', rid)
        for field, limit in [('title', 200), ('body', 30000), ('reference', 30000)]:
            value = row.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ImportFailure('DATASET_INVALID_TEXT', rid)
        facts = row.get('requiredFacts')
        if not isinstance(facts, list) or not 1 <= len(facts) <= 100 or any(
                not isinstance(f, str) or not f.strip() or len(f) > 30000 for f in facts):
            raise ImportFailure('DATASET_INVALID_FACTS', rid)
        if row['title'] in titles or row['body'] in bodies:
            raise ImportFailure('DATASET_DUPLICATE_CONTENT', rid)
        titles.add(row['title'])
        bodies.add(row['body'])
    if ids != {f'GT-SYN-{i:03d}' for i in range(1, 101)}:
        raise ImportFailure('DATASET_INVALID_ID_SET')
    return rows, sha


def validate_site(site):
    parts = urlsplit(site)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or
            parts.password or parts.query or parts.fragment or parts.path not in ('', '/')):
        raise ImportFailure('SITE_MUST_BE_HTTPS_ORIGIN')
    return site.rstrip('/')


class Client:
    def __init__(self, site, token):
        self.site = validate_site(site)
        if not token or len(token) > 8192 or any(c.isspace() for c in token):
            raise ImportFailure('INVALID_TOKEN_INPUT')
        self._token = token
        self._connection = None

    def close(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _connect(self):
        target = urlsplit(self.site)
        proxy_url = None if proxy_bypass(target.hostname) else getproxies().get('https')
        if proxy_url:
            proxy = urlsplit(proxy_url)
            # HTTPSConnection performs CONNECT to this HTTP proxy, then validates
            # the TLS certificate of the fixed target. Proxy credentials are not used.
            if (proxy.scheme != 'http' or not proxy.hostname or proxy.username or
                    proxy.password or proxy.query or proxy.fragment or proxy.path not in ('', '/')):
                raise ImportFailure('UNSUPPORTED_HTTPS_PROXY')
            connection = http.client.HTTPSConnection(proxy.hostname, proxy.port or 80, timeout=60)
            connection.set_tunnel(target.hostname, target.port or 443)
        else:
            connection = http.client.HTTPSConnection(target.hostname, target.port or 443, timeout=60)
        self._connection = connection
        return connection

    def request(self, method, path, payload=None):
        permitted = ((method == 'GET' and path in ('/api/data', '/api/experiments')) or
                     (method == 'GET' and re.fullmatch(r'/api/experiments/[0-9a-fA-F-]{36}', path)) or
                     (method == 'POST' and path == '/api/data/normal'))
        if not permitted:
            raise ImportFailure('ENDPOINT_NOT_PERMITTED')
        headers = {'OAI-Sites-Authorization': 'Bearer ' + self._token,
                   'Accept': 'application/json', 'Cache-Control': 'no-store'}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            headers['Content-Type'] = 'application/json'
        try:
            connection = self._connection or self._connect()
            # Reuse an established connection, but never replay a failed request.
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise ImportFailure('HTTP_REDIRECT_REFUSED')
            if not 200 <= response.status < 300:
                raise ImportFailure('HTTP_STATUS_' + str(response.status))
            raw = response.read(10 * 1024 * 1024 + 1)
            if len(raw) > 10 * 1024 * 1024:
                raise ImportFailure('HTTP_RESPONSE_TOO_LARGE')
            return json.loads(raw)
        except ImportFailure:
            self.close()
            raise
        except (http.client.HTTPException, TimeoutError, OSError):
            self.close()
            raise ImportFailure('HTTP_TRANSPORT_FAILURE') from None
        except (ValueError, UnicodeError):
            self.close()
            raise ImportFailure('HTTP_INVALID_JSON') from None


def dataset_snapshot(data):
    if not isinstance(data, dict):
        raise ImportFailure('API_INVALID_DATASETS')
    result = {}
    for kind in ('normal', 'attacks', 'categories'):
        rows = data.get(kind)
        if not isinstance(rows, list):
            raise ImportFailure('API_INVALID_DATASETS')
        indexed = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] in indexed:
                raise ImportFailure('API_DUPLICATE_OR_INVALID_ID')
            indexed[row['id']] = digest(row)
        result[kind] = indexed
    return result


def preserve(before, after):
    for kind, records in before.items():
        if any(after[kind].get(rid) != sha for rid, sha in records.items()):
            raise ImportFailure('PREEXISTING_' + kind.upper() + '_CHANGED')


def match_candidate(row, normal):
    # A reused title/body with different content is a collision, not a new row.
    candidates = [r for r in normal if r.get('title') == row['title'] or r.get('body') == row['body']]
    if not candidates:
        return None
    if len(candidates) != 1:
        raise ImportFailure('AMBIGUOUS_EXISTING_CONTENT', row['id'])
    candidate = candidates[0]
    fields = ('title', 'body', 'reference', 'requiredFacts', 'reviewStatus')
    if (any(candidate.get(k) != row[k] for k in fields) or
            candidate.get('provenance') not in ('user', 'synthetic')):
        raise ImportFailure('EXISTING_CONTENT_CONFLICT', row['id'])
    if (not isinstance(candidate.get('id'), str) or not isinstance(candidate.get('dbId'), str) or
            type(candidate.get('dbVersion')) is not int or candidate['dbVersion'] < 1):
        raise ImportFailure('API_MISSING_DB_MAPPING', row['id'])
    return candidate


def mapping(row, saved, status):
    return {'sourceId': row['id'], 'id': saved['id'], 'dbId': saved['dbId'],
            'dbVersion': saved['dbVersion'], 'status': status, 'apiRowSha256': digest(saved)}


def experiment_ids(data):
    if not isinstance(data, dict) or not isinstance(data.get('experiments'), list):
        raise ImportFailure('API_INVALID_EXPERIMENTS')
    ids = []
    for row in data['experiments']:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] in ids:
            raise ImportFailure('API_INVALID_EXPERIMENTS')
        ids.append(row['id'])
    return ids


def run_import(client, rows, apply, report):
    before = client.request('GET', '/api/data')
    original = dataset_snapshot(before)
    protected = {kind: dict(records) for kind, records in original.items()}
    prior_experiments = experiment_ids(client.request('GET', '/api/experiments'))
    report['before'] = {'counts': {k: len(v) for k, v in original.items()},
                        'rowHashes': original, 'experimentIds': prior_experiments,
                        'listedExperimentCount': len(prior_experiments), 'experimentListLimit': 100}
    # All 100 candidates are checked before the first mutation.
    matched = {row['id']: match_candidate(row, before['normal']) for row in rows}
    report['plannedMissing'] = sum(v is None for v in matched.values())
    report['matchedExisting'] = len(rows) - report['plannedMissing']
    for row in rows:
        saved = matched[row['id']]
        if saved:
            report['mappings'].append(mapping(row, saved, 'existing'))
    if not apply:
        report['status'] = 'dry-run-ready'
        return
    current = before
    checked_since_refresh = 0
    for row in rows:
        if matched[row['id']]:
            continue
        # POST returns the complete current dataset. Reuse it, refreshing every
        # ten candidates; never retry a POST. Only one importer may run at once.
        if checked_since_refresh >= 10:
            current = client.request('GET', '/api/data')
            checked_since_refresh = 0
        checked_since_refresh += 1
        preserve(protected, dataset_snapshot(current))
        saved = match_candidate(row, current['normal'])
        if saved:
            report['mappings'].append(mapping(row, saved, 'concurrently-added'))
            protected['normal'][saved['id']] = digest(saved)
            continue
        payload = {key: row[key] for key in ('title', 'body', 'reference', 'requiredFacts', 'reviewStatus')}
        report['uncertainSourceId'] = row['id']
        response = client.request('POST', '/api/data/normal', payload)
        preserve(protected, dataset_snapshot(response))
        current = response
        saved = match_candidate(row, response['normal'])
        if not saved:
            raise ImportFailure('POST_RECORD_NOT_VISIBLE', row['id'])
        report['mappings'].append(mapping(row, saved, 'created'))
        report['created'] += 1
        protected['normal'][saved['id']] = digest(saved)
        report.pop('uncertainSourceId', None)
        if report['created'] % 10 == 0:
            print(json.dumps({'status': 'importing', 'created': report['created'],
                              'lastSourceId': row['id']}), file=sys.stderr, flush=True)
    after = client.request('GET', '/api/data')
    final_snapshot = dataset_snapshot(after)
    preserve(protected, final_snapshot)
    for row in rows:
        if not match_candidate(row, after['normal']):
            raise ImportFailure('FINAL_RECORD_MISSING', row['id'])
    final_experiments = experiment_ids(client.request('GET', '/api/experiments'))
    checked_details = []
    for eid in set(prior_experiments) - set(final_experiments):
        detail = client.request('GET', '/api/experiments/' + eid)
        if not isinstance(detail, dict) or detail.get('experiment', {}).get('id') != eid:
            raise ImportFailure('PREEXISTING_EXPERIMENT_MISSING')
        checked_details.append(eid)
    report['after'] = {'counts': {k: len(v) for k, v in final_snapshot.items()},
                       'rowHashes': final_snapshot, 'experimentIds': final_experiments,
                       'listedExperimentCount': len(final_experiments), 'experimentListLimit': 100,
                       'olderExperimentIdsVerifiedByDetail': sorted(checked_details)}
    report['status'] = 'verified'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', type=Path, help='Path to emails.json (manifest.json must be alongside it)')
    parser.add_argument('--site', required=True, help='Exact HTTPS site origin, no path')
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--apply', action='store_true', help='Add missing rows; default is read-only dry-run')
    args = parser.parse_args(argv)
    report = {'schemaVersion': 1, 'startedAt': datetime.now(timezone.utc).isoformat(),
              'mode': 'apply' if args.apply else 'dry-run', 'status': 'started', 'created': 0,
              'mappings': [], 'modelCalls': 0,
              'verificationLimitations': [
                  'API does not expose raw DB provenance or ground-truth review_status; current backend stores synthetic/draft.',
                  'Experiment listing is capped at 100; only observed preexisting IDs are checked.',
                  'Existing POST has no idempotency key; run only one importer at a time.']}
    exit_code = 0
    try:
        rows, sha = load_candidates(args.dataset)
        report.update(datasetSha256=sha, recordCount=len(rows))
        site = validate_site(args.site)
        token = getpass.getpass('Site access token: ') if sys.stdin.isatty() else sys.stdin.readline().rstrip('\r\n')
        run_import(Client(site, token), rows, args.apply, report)
    except ImportFailure as error:
        report['status'] = 'stopped'
        report['errorCode'] = error.code
        if error.source_id:
            report['errorSourceId'] = error.source_id
        exit_code = 1
    except (Exception, KeyboardInterrupt):
        report['status'], report['errorCode'] = 'stopped', 'LOCAL_OR_INPUT_FAILURE'
        exit_code = 1
    report['finishedAt'] = datetime.now(timezone.utc).isoformat()
    try:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    except OSError:
        print('REPORT_WRITE_FAILED', file=sys.stderr)
        return 1
    print(json.dumps({'status': report['status'], 'created': report['created'],
                      'mapped': len(report['mappings']), 'errorCode': report.get('errorCode'),
                      'report': str(args.report)}, ensure_ascii=False))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
