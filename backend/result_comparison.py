"""Read-only, selected-record comparisons over frozen input cohorts.

The cohort is independent of outcomes: pre-model blocks, failures and pending
runs remain visible. No current dataset row or transformed request hash is used
to pair an OFF run with an ON run.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from math import isfinite
from statistics import mean

from backend.database import DataError, digest, text, uuid


MODES = ('off', 'filter', 'separate', 'both')
MAX_RUNS = 10000
DEFENSE_SQL = """CASE
    WHEN c.input_snapshot->>'defenseMode' IS NOT NULL
        AND c.input_snapshot#>>'{defensePlan,defenseResult,mode}' IS NOT NULL
        AND c.input_snapshot->>'defenseMode'<>c.input_snapshot#>>'{defensePlan,defenseResult,mode}' THEN 'unknown'
    WHEN NOT r.security_gateway_enabled
        AND coalesce(c.input_snapshot->>'defenseMode',c.input_snapshot#>>'{defensePlan,defenseResult,mode}','off')='off' THEN 'off'
    WHEN r.security_gateway_enabled
        AND coalesce(c.input_snapshot->>'defenseMode',c.input_snapshot#>>'{defensePlan,defenseResult,mode}') IN ('filter','separate','both')
        THEN coalesce(c.input_snapshot->>'defenseMode',c.input_snapshot#>>'{defensePlan,defenseResult,mode}')
    ELSE 'unknown' END"""


def validate(data):
    if not isinstance(data, dict) or set(data) - {'kind', 'selections', 'vector'}:
        raise DataError('비교 종류와 선택한 실험 기록을 확인해 주세요.')
    kind = data.get('kind')
    vector = data.get('vector', 'all')
    if kind not in ('normal', 'attack'):
        raise DataError('비교 종류는 normal 또는 attack이어야 합니다.')
    if vector not in ('all', 'direct', 'indirect') or (kind == 'normal' and vector != 'all'):
        raise DataError('공격 경로는 공격 실험 비교에서 선택해 주세요.')
    selections = data.get('selections')
    if not isinstance(selections, list) or not 2 <= len(selections) <= (4 if kind == 'normal' else 5):
        raise DataError('정상 평가는 2~4개 조건, 공격 실험은 2~5개 모델의 기록을 선택해 주세요.')
    chosen = []
    for selection in selections:
        if not isinstance(selection, dict) or set(selection) != {'experimentId', 'model', 'defense'}:
            raise DataError('각 기록의 실험 ID, 모델, 방어 모드를 지정해 주세요.')
        model = text(selection['model'], '모델', 200)
        if model != model.strip() or selection['defense'] not in MODES:
            raise DataError('저장된 모델과 방어 모드를 확인해 주세요.')
        chosen.append({'experimentId': uuid(selection['experimentId']), 'model': model,
                       'defense': selection['defense']})
    if len({tuple(s.values()) for s in chosen}) != len(chosen):
        raise DataError('같은 실험 기록을 중복 선택할 수 없습니다.')
    modes = [s['defense'] for s in chosen]
    models = [s['model'] for s in chosen]
    if kind == 'normal':
        if len(set(models)) != 1 or modes.count('off') != 1 or len(set(modes)) != len(modes):
            raise DataError('같은 모델의 방어 OFF 기록 1개와 서로 다른 방어 ON 조건을 선택해 주세요.')
    elif len(set(models)) != len(models) or len(set(modes)) != 1 or modes[0] == 'off':
        raise DataError('서로 다른 2~5개 모델과 동일한 방어 ON 조건을 선택해 주세요.')
    return kind, chosen, vector


def records(db, kind='normal', page=1, page_size=50):
    if kind not in ('normal', 'attack'):
        raise DataError('비교 종류는 normal 또는 attack이어야 합니다.')
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
        raise DataError('페이지는 1 이상, 표시 개수는 1~100으로 지정해 주세요.')
    purpose = 'performance' if kind == 'normal' else 'attack'
    grouped = '''SELECT e.id,e.name,e.created_at,e.status,c.requested_model AS model,
        ''' + DEFENSE_SQL + ''' AS defense,count(*)::int AS run_count
        FROM experiments e JOIN experiment_cases c ON c.experiment_id=e.id
        JOIN runs r ON r.experiment_case_id=c.id WHERE e.purpose=%s
        GROUP BY e.id,c.requested_model,defense'''
    with db.connect() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        total = conn.execute('SELECT count(*)::int AS total FROM (' + grouped + ') g', (purpose,)).fetchone()['total']
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = conn.execute(grouped + ' ORDER BY e.created_at DESC,e.id DESC,model,defense LIMIT %s OFFSET %s',
                            (purpose, page_size, (page - 1) * page_size)).fetchall()
    return {'records': [{'experimentId': str(r['id']), 'name': r['name'], 'createdAt': r['created_at'],
                         'status': r['status'], 'model': r['model'], 'defense': r['defense'],
                         'runCount': r['run_count']} for r in rows],
            'page': page, 'pageSize': page_size, 'total': total, 'totalPages': pages}


def _required(mapping, names):
    if not isinstance(mapping, dict) or any(mapping.get(name) is None or mapping.get(name) == '' for name in names):
        raise ValueError('원본 입력 또는 버전 정보 누락')
    return {name: mapping[name] for name in names}


def cohort_key(row, kind):
    """Return (original case identity, comparison identity, repeat slot)."""
    snapshot = row.get('input_snapshot')
    _required(snapshot, ('inputMode', 'messages'))
    base = _required(row, ('provider', 'prompt_version', 'system_prompt', 'inference_settings', 'evaluation_policy'))
    base['inputMode'] = snapshot['inputMode']
    base['injectionStrategy'] = snapshot.get('injectionStrategy')
    repeat = row.get('repeat_index')
    if type(repeat) is not int or repeat < 1:
        raise ValueError('반복 번호 누락')
    if row['defense'] != 'off':
        plan = _required(snapshot.get('defensePlan'), ('originalMessages', 'defenseResult'))
        original = plan['originalMessages']
        _required(plan['defenseResult'], ('version', 'mode'))
        if plan['defenseResult']['mode'] != row['defense'] or not row.get('defense_profile_id'):
            raise ValueError('방어 스냅샷 불일치')
        if kind == 'attack':
            base['defenseProfile'] = str(row['defense_profile_id'])
            base['defenseVersion'] = plan['defenseResult']['version']
            base['defenseCode'] = row.get('defense_code_ref')
            base['defenseSteps'] = row.get('defense_steps')
    else:
        original = snapshot['messages']
    if not isinstance(original, list) or not original:
        raise ValueError('원본 메시지 누락')
    base['originalMessages'] = original
    if kind == 'normal':
        if row.get('expected_label') != 'normal':
            raise ValueError('정상 이메일 라벨 불일치')
        performance = _required(snapshot.get('performance'), (
            'datasetId', 'datasetSha256', 'datasetVersion', 'sourceId', 'emailId', 'caseKey', 'emailRevision',
            'groundTruthId', 'groundTruthVersion', 'reference', 'requiredFacts', 'contentSha256', 'scorerVersion'))
        email = _required(snapshot.get('email'), ('subject', 'body'))
        if digest({'title': email['subject'], 'body': email['body'], 'reference': performance['reference'],
                   'requiredFacts': performance['requiredFacts']}) != performance['contentSha256']:
            raise ValueError('원본 내용 해시 불일치')
        performance['factEvidence'] = snapshot['performance'].get('factEvidence')
        base['performance'] = performance
        case = (performance['datasetId'], performance['sourceId'])
    else:
        if row.get('expected_label') != 'attack':
            raise ValueError('공격 시나리오 라벨 불일치')
        scenario = _required(snapshot.get('attackScenario'), ('id', 'key', 'revision', 'vector', 'channel', 'payload'))
        if scenario['vector'] not in ('direct', 'indirect'):
            raise ValueError('공격 경로 누락')
        base['scenario'] = scenario
        base['successCriterion'] = _required(snapshot.get('successCriterion'), ('type',))
        # Preserve all criterion fields (expected output, canary, rubric, etc.).
        base['successCriterion'] = snapshot['successCriterion']
        attachments = snapshot.get('attachments', [])
        if not isinstance(attachments, list) or (snapshot['inputMode'] == 'attachment-v1' and not attachments):
            raise ValueError('첨부파일 스냅샷 누락')
        base['attachments'] = []
        for attachment in attachments:
            item = _required(attachment, ('id', 'sha256', 'textSha256', 'method', 'extractorVersion', 'text'))
            base['attachments'].append(item)
        case = ('attack', scenario['key'])
    return digest(case), digest(base), repeat


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if isfinite(result) and result >= 0 else None


def metrics(rows, kind):
    count = Counter(r['status'] for r in rows)
    finished = [r for r in rows if r['status'] in ('succeeded', 'blocked')]
    determined = [r for r in finished if r.get('decision_status') == 'determinate']
    successes = sum(r.get('boolean_value') is True for r in determined) if kind == 'attack' else 0
    failures = sum(r.get('boolean_value') is False for r in determined) if kind == 'attack' else 0
    judged = successes + failures
    refusal = lambda r: r['status'] != 'blocked' and ((r.get('details') or {}).get('responseSignal') == 'refusal_candidate'
                                                    or (r.get('details') or {}).get('reason') == 'model_refusal')
    refusals = sum(refusal(r) for r in finished)
    quality = [r for r in determined if _number(r.get('score')) is not None] if kind == 'normal' else []
    facts = [_number((r.get('details') or {}).get('factCoverage')) for r in quality]
    facts = [value for value in facts if value is not None]
    actual = [r for r in rows if r['status'] in ('succeeded', 'failed', 'timed_out') and r.get('call_count', 0) > 0]
    latency = [_number(r.get('latency_ms')) for r in actual]
    latency = [value for value in latency if value is not None]
    tokens = [[_number(r.get(key)) for key in ('input_tokens', 'output_tokens', 'total_tokens')] for r in actual]
    tokens = [values for values in tokens if all(value is not None for value in values)]
    return {'total': len(rows), 'completed': count['succeeded'], 'blocked': count['blocked'],
            'refusalCandidates': refusals, 'errors': count['failed'] + count['timed_out'],
            'pending': count['queued'] + count['running'], 'cancelled': count['cancelled'],
            'judged': judged if kind == 'attack' else len(quality), 'successes': successes, 'failures': failures,
            'indeterminate': sum(r.get('decision_status') == 'indeterminate' for r in finished),
            'unjudged': len(finished) - judged - sum(r.get('decision_status') == 'indeterminate' for r in finished) if kind == 'attack'
                        else len(finished) - len(quality) - sum(r.get('decision_status') == 'indeterminate' for r in finished),
            'asr': 100 * successes / judged if judged else None, 'gradedCount': len(quality),
            'meanRougeL': mean(float(r['score']) for r in quality) if quality else None,
            'meanFactCoverage': mean(facts) if facts else None,
            'normalRejectionRate': 100 * (count['blocked'] + refusals) / len(finished) if kind == 'normal' and finished else None,
            'latencyMsMean': mean(latency) if latency else None, 'latencyCount': len(latency),
            'inputTokensMean': mean(v[0] for v in tokens) if tokens else None,
            'outputTokensMean': mean(v[1] for v in tokens) if tokens else None,
            'totalTokensMean': mean(v[2] for v in tokens) if tokens else None, 'tokensCount': len(tokens)}


def compare_rows(kind, selections, rows, vector='all'):
    """Pure comparison; DB adapter supplies latest effective judgments and usage."""
    warnings = []
    groups = []
    maps = []
    for selected in selections:
        chosen = {str(r['id']): r for r in rows if str(r['experiment_id']) == selected['experimentId']
                  and r['requested_model'] == selected['model'] and r['defense'] == selected['defense']}
        if not chosen:
            raise DataError('선택한 종류·모델·방어 조건의 실험 기록을 찾을 수 없습니다.', 404, 'COMPARISON_RECORD_NOT_FOUND')
        name = next(iter(chosen.values()))['name']
        def selected_vector(row):
            snapshot = row.get('input_snapshot')
            scenario = snapshot.get('attackScenario') if isinstance(snapshot, dict) else None
            return scenario.get('vector') if isinstance(scenario, dict) else None
        filtered = [r for r in chosen.values() if vector == 'all' or selected_vector(r) == vector]
        slots = defaultdict(list)
        excluded = Counter()
        for row in filtered:
            try:
                case, identity, repeat = cohort_key(row, kind)
                slots[(identity, repeat)].append((case, row))
            except (ValueError, TypeError, KeyError) as error:
                excluded[str(error) if isinstance(error, ValueError) else '지원되지 않는 스냅샷'] += 1
        ambiguous = sum(len(values) for values in slots.values() if len(values) != 1)
        if ambiguous:
            excluded['동일 입력·반복 번호가 중복된 실행'] += ambiguous
        maps.append({key: values[0] for key, values in slots.items() if len(values) == 1})
        for reason, count in sorted(excluded.items()):
            warnings.append(f'{name} / {selected["model"]} / {selected["defense"]}: {reason} {count}건 제외')
        groups.append({**selected, 'name': name, 'selectedRuns': len(filtered)})
    common = set.intersection(*(set(mapping) for mapping in maps))
    common_cases = {maps[0][key][0] for key in common}
    for group, mapping in zip(groups, maps):
        group.update(matchedRuns=len(common), excludedRuns=group['selectedRuns'] - len(common),
                     metrics=metrics([mapping[key][1] for key in sorted(common)], kind))
        if group['excludedRuns']:
            warnings.append(f'{group["name"]} / {group["model"]} / {group["defense"]}: 선택 {group["selectedRuns"]}건 중 공통 입력·설정·반복 번호 {len(common)}건으로 비교합니다.')
    if not common:
        warnings.append('원본 입력·자료 버전·기준 정답/판정 설정·프롬프트·생성 설정·반복 번호가 모두 같은 공통 실행이 없습니다.'
                        + (' 공격 비교는 방어 구현 버전도 같아야 합니다.' if kind == 'attack' else ''))
    if any(g['metrics']['pending'] or g['metrics']['errors'] or g['metrics']['cancelled'] for g in groups):
        warnings.append('대기·오류·취소 실행은 공통 입력 수에 포함하되 성공률·품질 점수에는 넣지 않습니다. 실행 완료 및 판정 수를 함께 확인해 주세요.')
    if any(g['metrics']['indeterminate'] or g['metrics']['unjudged'] for g in groups):
        warnings.append('판정 보류·미채점 기록은 성공률·품질 평균의 분모에서 제외됩니다.')
    warnings.append('토큰은 각 모델/API가 보고한 사용량이며 모델 간 동일한 길이 단위가 아닙니다. 호출 지연·토큰 평균에는 모델 호출 전 차단을 포함하지 않습니다.')
    if kind == 'normal':
        warnings.append('정상 거부율은 완료·차단 실행 중 입력 차단 또는 저장된 모델 거부 후보의 비율입니다. ROUGE-L은 자동 유사도 지표이며 정답의 의미적 정확성을 보장하지 않습니다.')
    return {'kind': kind, 'generatedAt': datetime.now(timezone.utc), 'comparable': bool(common),
            'commonCases': len(common_cases), 'commonRuns': len(common), 'warnings': warnings, 'groups': groups}


def compare(db, data):
    kind, selections, vector = validate(data)
    purpose = 'performance' if kind == 'normal' else 'attack'
    clauses, params = [], [purpose]
    for item in selections:
        clauses.append('(e.id=%s AND c.requested_model=%s AND (' + DEFENSE_SQL + ')=%s)')
        params.extend((item['experimentId'], item['model'], item['defense']))
    query = '''SELECT r.id,r.status,r.defense_profile_id,r.call_log_complete,
        c.experiment_id,c.requested_model,c.expected_label,c.repeat_index,c.provider,
        c.prompt_version,c.system_prompt,c.inference_settings,c.input_snapshot,e.name,e.evaluation_policy,
        ''' + DEFENSE_SQL + ''' AS defense,p.code_ref AS defense_code_ref,p.ordered_steps AS defense_steps,
        v.decision_status,v.boolean_value,v.score,v.details,
        calls.call_count,calls.latency_ms,calls.input_tokens,calls.output_tokens,calls.total_tokens
        FROM experiments e JOIN experiment_cases c ON c.experiment_id=e.id
        JOIN runs r ON r.experiment_case_id=c.id LEFT JOIN defense_profiles p ON p.id=r.defense_profile_id
        LEFT JOIN LATERAL (
            SELECT decision_status,boolean_value,score,details FROM evaluations
            WHERE run_id=r.id AND metric=%s
            ORDER BY (judge_type='human') DESC,created_at DESC,id DESC LIMIT 1
        ) v ON true
        LEFT JOIN LATERAL (
            SELECT count(*)::int AS call_count,
                CASE WHEN r.call_log_complete AND count(*)>0 AND count(latency_ms)=count(*) THEN sum(latency_ms) END AS latency_ms,
                CASE WHEN r.call_log_complete AND count(*)>0 AND count(input_tokens)=count(*) THEN sum(input_tokens) END AS input_tokens,
                CASE WHEN r.call_log_complete AND count(*)>0 AND count(output_tokens)=count(*) THEN sum(output_tokens) END AS output_tokens,
                CASE WHEN r.call_log_complete AND count(*)>0 AND count(total_tokens)=count(*) THEN sum(total_tokens) END AS total_tokens
            FROM llm_calls WHERE run_id=r.id AND purpose IN ('summarize','security')
                AND status NOT IN ('prepared','not_sent','cancelled')
        ) calls ON true
        WHERE e.purpose=%s AND (''' + ' OR '.join(clauses) + ') ORDER BY r.id LIMIT %s'
    with db.connect() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        rows = conn.execute(query, ('summary_quality' if kind == 'normal' else 'attack_success', *params, MAX_RUNS + 1)).fetchall()
    if len(rows) > MAX_RUNS:
        raise DataError('한 번에 비교할 수 있는 실행 기록은 10,000건까지입니다. 더 작은 실험 기록을 선택해 주세요.', 413, 'COMPARISON_TOO_LARGE')
    return compare_rows(kind, selections, rows, vector)
