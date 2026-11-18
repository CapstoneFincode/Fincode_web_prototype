#!/usr/bin/env python3
"""Approve the exact imported synthetic set on explicit user instruction.

Defaults to a read-only preview. This records user approval, not human inspection.
The API checks all versions/hashes before a single atomic status-only update.
"""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ground_truth_import', ROOT / 'scripts/import-ground-truth.py')
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)
REVIEW_FIELDS = {'reviewStatus', 'referenceReviewStatus', 'referenceReviewNote'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def prepare(data):
    source = json.loads((ROOT / 'datasets/ground_truth_set/v1/emails.json').read_text())
    mappings = json.loads((ROOT / 'docs/verification/2026-11-09-model-evaluation-import.json').read_text())['mappings']
    by_source = {row['sourceId']: row for row in mappings}
    normal = {row['id']: row for row in data['normal']}
    if len(source) != 100 or len(by_source) != 100 or len(normal) != len(data['normal']):
        raise ValueError('UNEXPECTED_DATASET_SIZE_OR_DUPLICATES')
    targets = []
    for original in source:
        current = normal[by_source[original['id']]['id']]
        fields = ('title', 'body', 'reference', 'requiredFacts')
        if any(current[key] != original[key] for key in fields) or current.get('provenance') == 'sample':
            raise ValueError('SOURCE_CONTENT_CHANGED:' + original['id'])
        content_hash = digest({key: current[key] for key in fields})
        if current.get('reviewContentSha256', content_hash) != content_hash:
            raise ValueError('SERVER_CONTENT_HASH_MISMATCH')
        targets.append((original['id'], current, content_hash))
    return targets


def protected_snapshot(data, target_ids, labels_only=False):
    ignored = REVIEW_FIELDS | ({'dbId', 'dbVersion', 'groundTruthId', 'groundTruthVersion'} if labels_only else set())
    return {kind: {row['id']: digest({key: value for key, value in row.items()
        if not (kind == 'normal' and row['id'] in target_ids and key in ignored)})
        for row in data[kind]} for kind in ('normal', 'attacks', 'categories')}


def write_request(client, method, path, payload):
    if not ((method == 'POST' and path == '/api/data/normal/review') or
            (method == 'PUT' and re.fullmatch(r'/api/data/normal/GT-[0-9a-f-]{36}', path))):
        raise ValueError('WRITE_ENDPOINT_NOT_PERMITTED')
    connection = client._connection or client._connect()
    connection.request(method, path, body=json.dumps(payload, ensure_ascii=False).encode(), headers={
        'Content-Type': 'application/json', 'Accept': 'application/json',
        'OAI-Sites-Authorization': 'Bearer ' + client._token})
    response = connection.getresponse()
    raw = response.read(10 * 1024 * 1024)
    if response.status != 200:
        raise RuntimeError('REVIEW_HTTP_STATUS_' + str(response.status))
    return json.loads(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--labels-only', action='store_true', help='Use the existing versioned normal-email API; reference approval remains pending.')
    args = parser.parse_args()
    client = transport.Client(args.site, getpass.getpass('Site access token: '))
    try:
        before = client.request('GET', '/api/data')
        targets = prepare(before)
        ids = {row['id'] for _, row, _ in targets}
        protected = protected_snapshot(before, ids, args.labels_only)
        report = {'startedAt': datetime.now(timezone.utc).isoformat(), 'mode': 'apply' if args.apply else 'preview',
            'approvalBasis': 'explicit-user-request', 'inspection': 'AI-assisted; not a claim of individual human inspection',
            'targetCount': len(targets), 'beforeLabelReviewed': sum(row['reviewStatus'] == 'reviewed' for _, row, _ in targets),
            'beforeReferenceReviewed': sum(row.get('referenceReviewStatus') == 'reviewed' for _, row, _ in targets)}
        if args.apply and args.labels_only:
            report.update(scope='normal-label-only', status='in-progress', changed=[])
            for sid, row, sha in targets:
                if row['reviewStatus'] == 'reviewed':
                    continue
                payload = {key: row[key] for key in ('title', 'body', 'reference', 'requiredFacts', 'dbVersion')}
                payload['reviewStatus'] = 'reviewed'
                updated = write_request(client, 'PUT', '/api/data/normal/' + row['id'], payload)
                saved = next(candidate for candidate in updated['normal'] if candidate['id'] == row['id'])
                if saved['reviewStatus'] != 'reviewed' or saved['dbVersion'] != row['dbVersion'] + 1:
                    raise RuntimeError('LABEL_REVIEW_WRITE_RESPONSE_MISMATCH')
                report['changed'].append({'sourceId': sid, 'id': row['id'], 'beforeDbId': row['dbId'],
                    'beforeVersion': row['dbVersion'], 'dbId': saved['dbId'], 'dbVersion': saved['dbVersion'], 'contentSha256': sha})
                Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            after = client.request('GET', '/api/data')
            reviewed = prepare(after)
            if protected_snapshot(after, ids, True) != protected or any(row['reviewStatus'] != 'reviewed' for _, row, _ in reviewed):
                raise RuntimeError('LABEL_REVIEW_READBACK_OR_PRESERVATION_FAILED')
            report.update(status='verified', labelReviewed=100, referenceApproval='pending-backend-deployment',
                contentAndOtherDataPreserved=True)
        elif args.apply:
            items = [{'id': row['id'], 'dbVersion': row['dbVersion'], 'groundTruthId': row['groundTruthId'],
                'groundTruthVersion': row['groundTruthVersion'], 'contentSha256': sha} for _, row, sha in targets]
            note = '김태현 사용자의 2026-11-09 요청에 따른 합성 정상 이메일 100건 및 기준 요약 승인. AI 보조 구조·원문 근거 점검을 수행했으며 사람이 100건을 개별 검수했다는 의미는 아님. 기준 요약 유사도와 필수 핵심 정보 확인은 별도 평가함.'
            report['result'] = write_request(client, 'POST', '/api/data/normal/review', {'items': items, 'note': note})
            after = client.request('GET', '/api/data')
            reviewed = prepare(after)
            if protected_snapshot(after, ids) != protected:
                raise RuntimeError('UNEXPECTED_CONTENT_OR_OTHER_DATA_CHANGE')
            if any(row['reviewStatus'] != 'reviewed' or row.get('referenceReviewStatus') != 'reviewed' for _, row, _ in reviewed):
                raise RuntimeError('REVIEW_READBACK_FAILED')
            report.update(status='verified', labelReviewed=100, referenceReviewed=100,
                contentAndOtherDataPreserved=True,
                mappings=[{'sourceId': sid, 'id': row['id'], 'dbId': row['dbId'], 'dbVersion': row['dbVersion'],
                    'groundTruthId': row['groundTruthId'], 'groundTruthVersion': row['groundTruthVersion'],
                    'contentSha256': sha} for sid, row, sha in reviewed])
        else:
            report['status'] = 'preview-verified'
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({key: value for key, value in report.items() if key not in ('mappings', 'result', 'changed')}, ensure_ascii=False))
    finally:
        client.close()


if __name__ == '__main__':
    main()
