"""Read-only pagination and model summaries for recorded web experiments.

These queries never inspect prompt/response bodies or alter runs/evaluations.
Execution types stay separate and only defense-OFF runs contribute to metrics.
"""
from datetime import datetime, timezone
from backend.database import DataError, text


PURPOSES = ('attack', 'summary', 'normal', 'performance')
VECTORS = ('all', 'direct', 'indirect')


def filters(purpose=None, vector='all', model=None):
    if purpose == 'all':
        purpose = None
    if purpose is not None and purpose not in PURPOSES:
        raise DataError('실험 종류를 확인해 주세요.')
    if vector not in VECTORS or (vector != 'all' and purpose not in (None, 'attack')):
        raise DataError('공격 경로는 공격 실험에서 선택해 주세요.')
    if model is not None:
        model = text(model, '모델', 200)
    return purpose, vector, model


def experiment_page(db, page=1, page_size=5, purpose=None, vector='all', model=None, provider=None):
    purpose, vector, model = filters(purpose, vector, model)
    if provider is not None:
        provider = text(provider, '제공자', 100)
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
        raise DataError('페이지는 1 이상, 표시 개수는 1~100으로 지정해 주세요.')
    where = '''(%s::text IS NULL OR e.purpose=%s)
        AND ((%s::text IS NULL AND %s::text IS NULL AND %s='all') OR EXISTS (
            SELECT 1 FROM experiment_cases c LEFT JOIN attack_scenarios s ON s.id=c.attack_scenario_id
            WHERE c.experiment_id=e.id AND (%s::text IS NULL OR c.requested_model=%s)
                AND (%s::text IS NULL OR c.provider=%s)
                AND (%s='all' OR (c.expected_label='attack'
                    AND coalesce(c.input_snapshot#>>'{attackScenario,vector}',s.vector)=%s))))'''
    params = (purpose, purpose, model, provider, vector, model, model, provider, provider, vector, vector)
    with db.connect() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        total = conn.execute('SELECT count(*)::int AS count FROM experiments e WHERE '+where, params).fetchone()['count']
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = conn.execute('''WITH selected AS (
            SELECT e.id,e.name,e.purpose,e.status,e.created_at FROM experiments e WHERE '''+where+'''
            ORDER BY e.created_at DESC,e.id DESC LIMIT %s OFFSET %s)
            SELECT e.*,count(r.id)::int AS total,
                count(r.id) FILTER (WHERE r.status NOT IN ('queued','running'))::int AS finished,
                coalesce(array_agg(DISTINCT c.requested_model ORDER BY c.requested_model)
                    FILTER (WHERE c.requested_model IS NOT NULL),ARRAY[]::text[]) AS models
            FROM selected e LEFT JOIN experiment_cases c ON c.experiment_id=e.id
            LEFT JOIN runs r ON r.experiment_case_id=c.id
            GROUP BY e.id,e.name,e.purpose,e.status,e.created_at
            ORDER BY e.created_at DESC,e.id DESC''', (*params, page_size, (page-1)*page_size)).fetchall()
        active = conn.execute("SELECT count(*)::int AS count FROM experiments WHERE status IN ('queued','running')").fetchone()['count']
    return {'experiments':rows, 'page':page, 'pageSize':page_size, 'total':total,
            'totalPages':pages, 'activeCount':active}


MODEL_QUERY = '''WITH selected AS (
    SELECT r.id,r.status,r.created_at,r.call_log_complete,c.experiment_id,c.provider,
        c.requested_model,c.expected_label,c.attack_scenario_id,c.prompt_version,c.inference_settings,
        coalesce(c.input_snapshot->>'inputMode','unknown') AS input_mode,
        coalesce(s.scenario_key,c.attack_scenario_id::text) AS scenario_key
    FROM runs r JOIN experiment_cases c ON c.id=r.experiment_case_id
    JOIN experiments e ON e.id=c.experiment_id
    LEFT JOIN attack_scenarios s ON s.id=c.attack_scenario_id
    WHERE e.purpose=%s AND NOT r.security_gateway_enabled
      AND (%s='all' OR (c.expected_label='attack'
        AND coalesce(c.input_snapshot#>>'{attackScenario,vector}',s.vector)=%s))
), measured AS (
    SELECT r.*,calls.latency_ms,calls.input_tokens,calls.output_tokens,calls.total_tokens,
        v.decision_status,v.boolean_value,v.score,v.evaluator_version,v.judge_type
    FROM selected r
    LEFT JOIN LATERAL (
        SELECT CASE WHEN r.call_log_complete AND count(*)>0 AND count(latency_ms)=count(*)
                    THEN sum(latency_ms) END AS latency_ms,
            CASE WHEN r.call_log_complete AND count(*)>0 AND count(input_tokens)=count(*)
                    THEN sum(input_tokens) END AS input_tokens,
            CASE WHEN r.call_log_complete AND count(*)>0 AND count(output_tokens)=count(*)
                    THEN sum(output_tokens) END AS output_tokens,
            CASE WHEN r.call_log_complete AND count(*)>0 AND count(total_tokens)=count(*)
                    THEN sum(total_tokens) END AS total_tokens
        FROM llm_calls WHERE run_id=r.id AND purpose IN ('summarize','security')
    ) calls ON true
    LEFT JOIN LATERAL (
        SELECT decision_status,boolean_value,score,evaluator_version,judge_type
        FROM evaluations WHERE run_id=r.id AND metric=%s
        ORDER BY (judge_type='human') DESC,created_at DESC,id DESC LIMIT 1
    ) v ON true
), versions AS (
    SELECT provider,requested_model,evaluator_version,count(*)::int AS count
    FROM measured WHERE status IN ('succeeded','blocked') AND evaluator_version IS NOT NULL
    GROUP BY provider,requested_model,evaluator_version
), returned AS (
    SELECT s.provider,s.requested_model,
        array_agg(DISTINCT c.returned_model ORDER BY c.returned_model) AS models
    FROM selected s JOIN llm_calls c ON c.run_id=s.id AND c.purpose='summarize'
    WHERE c.returned_model IS NOT NULL AND btrim(c.returned_model)<>''
    GROUP BY s.provider,s.requested_model
)
SELECT m.provider,m.requested_model AS model,
    count(DISTINCT m.experiment_id)::int AS experiments,count(*)::int AS total,
    count(*) FILTER (WHERE status='succeeded')::int AS succeeded,
    count(*) FILTER (WHERE status IN ('failed','timed_out'))::int AS errors,
    count(*) FILTER (WHERE status='queued')::int AS queued,
    count(*) FILTER (WHERE status='running')::int AS running,
    count(*) FILTER (WHERE status='cancelled')::int AS cancelled,
    count(*) FILTER (WHERE status='blocked')::int AS blocked,
    count(*) FILTER (WHERE expected_label='attack' AND status IN ('succeeded','blocked')
        AND decision_status='determinate' AND boolean_value IS TRUE)::int AS attack_successes,
    count(*) FILTER (WHERE expected_label='attack' AND status IN ('succeeded','blocked')
        AND decision_status='determinate' AND boolean_value IS FALSE)::int AS attack_failures,
    count(*) FILTER (WHERE expected_label='attack' AND status IN ('succeeded','blocked')
        AND decision_status='indeterminate')::int AS attack_indeterminate,
    count(*) FILTER (WHERE expected_label='attack' AND status IN ('succeeded','blocked')
        AND (decision_status IS NULL OR decision_status='not_applicable'))::int AS attack_unjudged,
    avg(latency_ms) FILTER (WHERE status='succeeded')::float8 AS latency_mean,
    count(latency_ms) FILTER (WHERE status='succeeded')::int AS latency_count,
    avg(input_tokens) FILTER (WHERE status='succeeded')::float8 AS input_mean,
    avg(output_tokens) FILTER (WHERE status='succeeded')::float8 AS output_mean,
    avg(total_tokens) FILTER (WHERE status='succeeded')::float8 AS total_mean,
    count(input_tokens) FILTER (WHERE status='succeeded')::int AS input_count,
    count(output_tokens) FILTER (WHERE status='succeeded')::int AS output_count,
    count(total_tokens) FILTER (WHERE status='succeeded')::int AS total_count,
    count(DISTINCT scenario_key)::int AS scenario_count,
    count(DISTINCT attack_scenario_id)::int AS scenario_version_count,
    array_agg(DISTINCT prompt_version ORDER BY prompt_version) AS prompt_versions,
    array_agg(DISTINCT input_mode ORDER BY input_mode) AS input_modes,
    count(DISTINCT inference_settings)::int AS inference_settings_count,
    min(m.created_at) AS first_run_at,max(m.created_at) AS last_run_at,
    count(*) FILTER (WHERE status='succeeded' AND decision_status='determinate' AND score IS NOT NULL)::int AS graded_count,
    avg(score) FILTER (WHERE status='succeeded' AND decision_status='determinate')::float8 AS mean_rouge_l,
    coalesce((SELECT models FROM returned x WHERE x.provider=m.provider AND x.requested_model=m.requested_model),ARRAY[]::text[]) AS returned_models,
    coalesce((SELECT jsonb_object_agg(v.evaluator_version,v.count) FROM versions v
        WHERE v.provider=m.provider AND v.requested_model=m.requested_model),'{}'::jsonb) AS evaluator_counts
FROM measured m GROUP BY m.provider,m.requested_model ORDER BY m.requested_model,m.provider'''


def model_results(db, purpose='attack', vector='all'):
    purpose, vector, _ = filters(purpose, vector)
    if purpose is None:
        raise DataError('모델 지표는 실험 종류를 하나 선택해 주세요.')
    metric = {'attack':'attack_success', 'normal':'normal_rejection', 'performance':'summary_quality'}.get(purpose, '')
    with db.connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        rows = conn.execute(MODEL_QUERY, (purpose, vector, vector, metric)).fetchall()
    models = []
    for row in rows:
        judged = row['attack_successes'] + row['attack_failures']
        models.append({
            **{key:row[key] for key in ('provider','model','experiments','total','succeeded','errors','queued','running','cancelled','blocked')},
            'returnedModels':row['returned_models'],
            'attack':{'successes':row['attack_successes'], 'failures':row['attack_failures'],
                      'indeterminate':row['attack_indeterminate'], 'unjudged':row['attack_unjudged'],
                      'judged':judged, 'asr':100*row['attack_successes']/judged if judged else None} if purpose=='attack' else None,
            'latencyMsMean':row['latency_mean'], 'latencyReportedCount':row['latency_count'],
            'tokens':{'inputMean':row['input_mean'], 'outputMean':row['output_mean'], 'totalMean':row['total_mean'],
                      'inputReportedCount':row['input_count'], 'outputReportedCount':row['output_count'], 'totalReportedCount':row['total_count']},
            'scenarioCount':row['scenario_count'], 'scenarioVersionCount':row['scenario_version_count'],
            'promptVersions':row['prompt_versions'], 'inputModes':row['input_modes'],
            'inferenceSettingsCount':row['inference_settings_count'],
            'evaluatorVersions':sorted(row['evaluator_counts']), 'evaluatorVersionCounts':row['evaluator_counts'],
            'firstRunAt':row['first_run_at'], 'lastRunAt':row['last_run_at'],
            'quality':{'gradedCount':row['graded_count'], 'meanRougeL':row['mean_rouge_l']} if purpose=='performance' else None,
        })
    return {'purpose':purpose, 'vector':vector, 'defense':'off',
            'generatedAt':datetime.now(timezone.utc), 'models':models}
