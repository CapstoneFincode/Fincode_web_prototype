"""Read-only comparisons over selected, frozen experiment records.

Normal evaluations share an input cohort. Attack cards are independent samples:
every selected run remains visible, even when cards have different conditions.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from math import isfinite
from statistics import mean

from backend.database import DataError, digest, text, uuid


MODES = ('off', 'filter', 'separate', 'both')
VECTORS = ('all', 'direct', 'indirect')
MAX_RUNS = 10000
VECTOR_SQL = """CASE
    WHEN c.input_snapshot#>>'{attackScenario,vector}' IN ('direct','indirect')
        THEN c.input_snapshot#>>'{attackScenario,vector}'
    WHEN c.input_snapshot#>>'{attackScenario,vector}' IS NULL THEN coalesce(s.vector,'unknown')
    ELSE 'unknown' END"""
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
    if vector not in VECTORS or (kind == 'normal' and vector != 'all'):
        raise DataError('공격 경로는 공격 실험 비교에서 선택해 주세요.')
    selections = data.get('selections')
    if not isinstance(selections, list) or not 2 <= len(selections) <= (4 if kind == 'normal' else 5):
        raise DataError('정상 평가는 2~4개 조건, 공격 실험은 2~5개 비교 카드의 기록을 선택해 주세요.')
    chosen = []
    for selection in selections:
        fields = {'experimentId', 'model', 'defense'}
        if not isinstance(selection, dict) or not fields <= set(selection) or set(selection) - fields - ({'vector'} if kind == 'attack' else set()):
            raise DataError('각 기록의 실험 ID, 모델, 방어 모드를 지정해 주세요.')
        model = text(selection['model'], '모델', 200)
        if model != model.strip() or selection['defense'] not in MODES:
            raise DataError('저장된 모델과 방어 모드를 확인해 주세요.')
        chosen.append({'experimentId': uuid(selection['experimentId']), 'model': model,
                       'defense': selection['defense']})
        if kind == 'attack':
            selected_vector = selection.get('vector', vector)
            if selected_vector not in VECTORS:
                raise DataError('각 카드의 공격 경로는 all, direct, indirect 중에서 선택해 주세요.')
            chosen[-1]['vector'] = selected_vector
    modes = [s['defense'] for s in chosen]
    models = [s['model'] for s in chosen]
    if kind == 'normal':
        if len({tuple(s.values()) for s in chosen}) != len(chosen):
            raise DataError('같은 실험 기록을 중복 선택할 수 없습니다.')
        if len(set(models)) != 1 or modes.count('off') != 1 or len(set(modes)) != len(modes):
            raise DataError('같은 모델의 방어 OFF 기록 1개와 서로 다른 방어 ON 조건을 선택해 주세요.')
    return kind, chosen, vector


def records(db, kind='normal', page=1, page_size=50):
    if kind not in ('normal', 'attack'):
        raise DataError('비교 종류는 normal 또는 attack이어야 합니다.')
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
        raise DataError('페이지는 1 이상, 표시 개수는 1~100으로 지정해 주세요.')
    purpose = 'performance' if kind == 'normal' else 'attack'
    vector_counts = ','.join('count(*) FILTER (WHERE (' + VECTOR_SQL + ")='" + value + "')::int AS vector_" + value
                             for value in ('direct', 'indirect', 'unknown')) if kind == 'attack' else ''
    grouped = '''SELECT e.id,e.name,e.created_at,e.status,c.requested_model AS model,
        ''' + DEFENSE_SQL + ''' AS defense,count(*)::int AS run_count''' + (',' + vector_counts if vector_counts else '') + '''
        FROM experiments e JOIN experiment_cases c ON c.experiment_id=e.id
        JOIN runs r ON r.experiment_case_id=c.id''' + (' LEFT JOIN attack_scenarios s ON s.id=c.attack_scenario_id' if kind == 'attack' else '') + ''' WHERE e.purpose=%s
        GROUP BY e.id,c.requested_model,defense'''
    with db.connect() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        total = conn.execute('SELECT count(*)::int AS total FROM (' + grouped + ') g', (purpose,)).fetchone()['total']
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = conn.execute(grouped + ' ORDER BY e.created_at DESC,e.id DESC,model,defense LIMIT %s OFFSET %s',
                            (purpose, page_size, (page - 1) * page_size)).fetchall()
    output = []
    for row in rows:
        record = {'experimentId': str(row['id']), 'name': row['name'], 'createdAt': row['created_at'],
                  'status': row['status'], 'model': row['model'], 'defense': row['defense'], 'runCount': row['run_count']}
        if kind == 'attack':
            record['vectorCounts'] = {value: row['vector_' + value] for value in ('direct', 'indirect', 'unknown')}
            record['vectors'] = [value for value, count in record['vectorCounts'].items() if count]
        output.append(record)
    return {'records': output,
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


def _scenario(row):
    snapshot = row.get('input_snapshot')
    value = snapshot.get('attackScenario') if isinstance(snapshot, dict) else None
    return value if isinstance(value, dict) else {}


def _saved_text(value):
    return value if isinstance(value, str) and value.strip() else None


def attack_vector(row):
    """Only missing legacy snapshots may fall back to their immutable FK row."""
    value = _scenario(row).get('vector')
    if value is None:
        value = row.get('legacy_vector')
    return value if value in ('direct', 'indirect') else 'unknown'


def attack_category(row):
    snapshot = row.get('input_snapshot')
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    scenario = _scenario(row)
    identifier = (_saved_text(scenario.get('categoryId')) or _saved_text(snapshot.get('attackCategoryId'))
                  or _saved_text(row.get('legacy_category_id')))
    # Never look up the current category name: it can change after this run.
    name = _saved_text(scenario.get('categoryName'))
    return identifier, name


def scenario_count(rows):
    identifiers = set()
    for row in rows:
        scenario = _scenario(row)
        key = _saved_text(scenario.get('key')) or _saved_text(row.get('legacy_scenario_key'))
        identifier = _saved_text(scenario.get('id')) or (str(row['legacy_scenario_id']) if row.get('legacy_scenario_id') else None)
        if key or identifier:
            identifiers.add(('key', key) if key else ('id', identifier))
    return len(identifiers)


def independent_attack(selections, rows, vector='all'):
    """Summarize each card independently; never intersect input sets across cards."""
    warnings = ['각 카드는 선택한 실험 기록과 공격 경로의 전체 실행을 독립 집계합니다. 입력·반복 수·방어·판정 설정이 다를 수 있으므로 동일 조건의 인과 비교로 해석하지 마세요.']
    groups, category_names, category_rows = [], defaultdict(set), defaultdict(dict)
    seen = {}
    for index, selected in enumerate(selections):
        selected_vector = selected.get('vector', vector)
        chosen = {str(row['id']): row for row in rows if str(row['experiment_id']) == selected['experimentId']
                  and row['requested_model'] == selected['model'] and row['defense'] == selected['defense']}
        if not chosen:
            raise DataError('선택한 종류·모델·방어 조건의 실험 기록을 찾을 수 없습니다.', 404, 'COMPARISON_RECORD_NOT_FOUND')
        signature = (selected['experimentId'], selected['model'], selected['defense'], selected_vector)
        if signature in seen:
            warnings.append(f'카드 {seen[signature] + 1}과 카드 {index + 1}은 같은 조건과 기록을 선택했습니다. 동일 실행을 각 카드에 표시합니다.')
        else:
            seen[signature] = index
        filtered = [row for row in chosen.values() if selected_vector == 'all' or attack_vector(row) == selected_vector]
        name = next(iter(chosen.values()))['name']
        groups.append({**selected, 'index': index, 'name': name, 'vector': selected_vector,
                       'sourceRuns': len(chosen), 'selectedRuns': len(filtered), 'excludedRuns': len(chosen) - len(filtered),
                       'matchedRuns': None, 'scenarioCount': scenario_count(filtered), 'metrics': metrics(filtered, 'attack')})
        if not filtered:
            warnings.append(f'카드 {index + 1}: 선택한 공격 경로에 해당하는 실행 기록이 없습니다.')
        unknown = sum(attack_vector(row) == 'unknown' for row in chosen.values())
        if unknown:
            warnings.append(f'카드 {index + 1}: 공격 경로 정보가 없는 {unknown}건은 전체 경로에서만 포함합니다.')
        missing_identity = sum(not (_saved_text(_scenario(row).get('key')) or _saved_text(_scenario(row).get('id'))
                                   or row.get('legacy_scenario_key') or row.get('legacy_scenario_id')) for row in filtered)
        if missing_identity:
            warnings.append(f'카드 {index + 1}: 시나리오 식별 정보가 없는 {missing_identity}건은 실행 지표에 포함하되 시나리오 수에서는 제외합니다.')
        grouped = defaultdict(list)
        for row in filtered:
            category, name = attack_category(row)
            grouped[category].append(row)
            if name:
                category_names[category].add(name)
        for category, members in grouped.items():
            category_rows[category][index] = members
    categories = []
    for category, by_group in category_rows.items():
        names = sorted(category_names[category])
        name = (' / '.join(names) if names else category) if category is not None else '미분류'
        if category is None and names:
            warnings.append('카테고리 ID가 없는 저장 이름은 분류를 확정할 수 없어 미분류로 집계합니다 (' + ' / '.join(names) + ').')
        elif len(names) > 1:
            warnings.append(f'카테고리 {category or "미분류"}: 같은 ID에 저장된 이름이 여러 개여서 한 행에 함께 표시합니다 ({name}).')
        categories.append({'id': category, 'name': name, 'groups': [
            {'index': index, 'scenarioCount': scenario_count(by_group.get(index, [])),
             'runCount': len(by_group.get(index, [])),
             'metrics': metrics(by_group[index], 'attack') if index in by_group else None}
            for index in range(len(groups))]})
    categories.sort(key=lambda category: (category['id'] is None, category['name'], category['id'] or ''))
    if any(group['metrics']['errors'] or group['metrics']['pending'] or group['metrics']['cancelled'] for group in groups):
        warnings.append('오류·대기·취소 실행은 실행 수에 포함하지만 공격 성공률의 분모에서는 제외합니다.')
    if any(group['metrics']['unjudged'] or group['metrics']['indeterminate'] for group in groups):
        warnings.append('미판정·판정 보류는 공격 성공률의 분모에서 제외합니다. 확정 성공과 확정 실패가 모두 없으면 성공률을 표시하지 않습니다.')
    warnings.append('전체 공격 성공률은 확정 성공 수 / (확정 성공 수 + 확정 실패 수)입니다. 카테고리별 성공률을 단순 평균하지 않습니다.')
    warnings.append('토큰은 각 모델/API가 보고한 사용량이며 모델 간 동일한 길이 단위가 아닙니다. 호출 지연·토큰 평균에는 모델 호출 전 차단을 포함하지 않습니다.')
    return {'kind': 'attack', 'analysisMode': 'independent', 'generatedAt': datetime.now(timezone.utc),
            'comparable': any(group['selectedRuns'] for group in groups), 'commonCases': None, 'commonRuns': None,
            'warnings': warnings, 'groups': groups, 'categories': categories}


def compare_rows(kind, selections, rows, vector='all'):
    """Pure comparison; DB adapter supplies latest effective judgments and usage."""
    if kind == 'attack':
        return independent_attack(selections, rows, vector)
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
        s.id AS legacy_scenario_id,s.scenario_key AS legacy_scenario_key,s.vector AS legacy_vector,s.category_id AS legacy_category_id,
        ''' + DEFENSE_SQL + ''' AS defense,p.code_ref AS defense_code_ref,p.ordered_steps AS defense_steps,
        v.decision_status,v.boolean_value,v.score,v.details,
        calls.call_count,calls.latency_ms,calls.input_tokens,calls.output_tokens,calls.total_tokens
        FROM experiments e JOIN experiment_cases c ON c.experiment_id=e.id
        JOIN runs r ON r.experiment_case_id=c.id LEFT JOIN defense_profiles p ON p.id=r.defense_profile_id
        LEFT JOIN attack_scenarios s ON s.id=c.attack_scenario_id
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
