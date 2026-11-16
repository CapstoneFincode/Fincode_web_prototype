"""Small, read-only overview of the current datasets and experiment metadata.

No model calls, result re-scoring, or experiment mutations occur here. Large
prompt/response snapshots are deliberately excluded from the reporting queries.
"""
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from backend.performance import DATASET_ID, source_dataset


CURRENT_EMAILS = '''SELECT DISTINCT ON (case_key)
    id,case_key,revision,subject,body,expected_label,provenance,label_review_status,archived_at
    FROM email_cases ORDER BY case_key,revision DESC'''


def ground_truth_summary(conn):
    """Match the same source content as performance evaluation, without returning it."""
    source, _, _ = source_dataset()
    rows = conn.execute('''WITH current_emails AS ('''+CURRENT_EMAILS+'''),
        source AS (SELECT * FROM jsonb_to_recordset(%s::jsonb)
            AS s(id text,title text,body text))
        SELECT s.id,count(e.id)::int AS matches,
            bool_and(e.label_review_status='reviewed') AS label_reviewed,
            bool_and(g.review_status='reviewed') AS reference_reviewed,
            bool_and(g.reference_summary IS NOT NULL AND btrim(g.reference_summary)<>'') AS has_reference
        FROM source s LEFT JOIN current_emails e
            ON e.subject=s.title AND e.body=s.body AND e.archived_at IS NULL
            AND e.provenance<>'sample' AND e.expected_label='normal'
            AND e.label_review_status<>'sample'
        LEFT JOIN LATERAL (SELECT review_status,reference_summary FROM ground_truths
            WHERE email_case_id=e.id ORDER BY revision DESC LIMIT 1) g ON true
        GROUP BY s.id''', (Jsonb([{'id':row['id'],'title':row['title'],'body':row['body']}
                                 for row in source]),)).fetchall()
    matched = [row for row in rows if row['matches'] == 1]
    ambiguous = sum(row['matches'] > 1 for row in rows)
    return {
        'id': DATASET_ID, 'total': len(source), 'available': len(matched),
        'reviewedLabels': sum(bool(row['label_reviewed']) for row in matched),
        'reviewedReferences': sum(bool(row['reference_reviewed']) for row in matched),
        'ready': 0 if ambiguous else sum(bool(row['label_reviewed'] and row['reference_reviewed']
                                              and row['has_reference']) for row in matched),
        'ambiguous': ambiguous,
        'error': '합성 평가 원본과 같은 내용의 DB 항목이 여러 개 있습니다. 중복 자료를 정리해 주세요.' if ambiguous else None,
    }


def recorded_defense(enabled, recorded, fallback):
    """Mirror public/attack-results.js recordedDefense for the overview labels."""
    valid = lambda value: isinstance(value, str) and value in ('off','filter','separate','both')
    if enabled is False:
        # JS truthiness: empty arrays/objects are truthy, unlike Python containers.
        truthy = recorded is not None and recorded is not False and recorded != 0 and recorded != ''
        return 'unknown' if truthy and recorded != 'off' else 'off'
    value = recorded if valid(recorded) else fallback if valid(fallback) else 'unknown'
    return 'unknown' if enabled is True and value == 'off' else value


def recent_experiments(conn, performance_only=False):
    # The predicate is selected by server code; no user-supplied SQL is accepted.
    predicate = "e.purpose='performance'" if performance_only else "e.purpose IN ('attack','normal','performance')"
    rows = conn.execute('''WITH selected AS (
        SELECT e.id,e.name,e.purpose,e.status,e.created_at,
            coalesce(e.execution_plan->>'defense','unknown') AS defense
        FROM experiments e WHERE '''+predicate+'''
        ORDER BY e.created_at DESC,e.id DESC LIMIT 5)
        SELECT e.*,coalesce(array_agg(DISTINCT c.requested_model ORDER BY c.requested_model)
            FILTER (WHERE c.requested_model IS NOT NULL),ARRAY[]::text[]) AS models
        FROM selected e LEFT JOIN experiment_cases c ON c.experiment_id=e.id
        GROUP BY e.id,e.name,e.purpose,e.status,e.created_at,e.defense
        ORDER BY e.created_at DESC,e.id DESC''').fetchall()
    modes = {row['id']:set() for row in rows}
    fallback = {row['id']:row['defense'] for row in rows}
    if rows:
        recorded = conn.execute('''SELECT DISTINCT c.experiment_id,r.security_gateway_enabled,
            coalesce(nullif(c.input_snapshot->'defenseMode','null'::jsonb),
                c.input_snapshot#>'{defensePlan,defenseResult,mode}') AS recorded_mode
            FROM experiment_cases c JOIN runs r ON r.experiment_case_id=c.id
            WHERE c.experiment_id=ANY(%s)''', ([row['id'] for row in rows],)).fetchall()
        for row in recorded:
            modes[row['experiment_id']].add(recorded_defense(row['security_gateway_enabled'],
                row['recorded_mode'],fallback[row['experiment_id']]))
    for row in rows:
        values = modes[row['id']]
        row['defense'] = 'mixed' if len(values)>1 else next(iter(values)) if values else recorded_defense(None,None,row['defense'])
    return [{'id':str(row['id']),'name':row['name'],'purpose':row['purpose'],
             'status':row['status'],'createdAt':row['created_at'].astimezone(timezone.utc).isoformat().replace('+00:00','Z'),
             'models':row['models'],'defense':row['defense']} for row in rows]


def overview(db):
    with db.connect() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        normal = conn.execute('''SELECT count(*)::int AS total FROM (SELECT DISTINCT ON (case_key)
            archived_at FROM email_cases ORDER BY case_key,revision DESC) e
            WHERE archived_at IS NULL''').fetchone()['total']
        attacks = conn.execute('''SELECT count(*)::int AS total,
            count(*) FILTER (WHERE vector='direct')::int AS direct,
            count(*) FILTER (WHERE vector='indirect')::int AS indirect
            FROM (SELECT DISTINCT ON (scenario_key) vector,archived_at
                FROM attack_scenarios ORDER BY scenario_key,revision DESC) a
            WHERE archived_at IS NULL''').fetchone()
        categories = conn.execute('''SELECT count(*)::int AS total FROM attack_categories
            WHERE archived_at IS NULL OR id IN (
                SELECT category_id FROM (SELECT DISTINCT ON (scenario_key) category_id,archived_at
                    FROM attack_scenarios ORDER BY scenario_key,revision DESC) a
                WHERE archived_at IS NULL)''').fetchone()['total']
        ground_truth = ground_truth_summary(conn)
        counts = conn.execute('''SELECT count(*)::int AS total,
            count(*) FILTER (WHERE status IN ('queued','running'))::int AS active,
            count(*) FILTER (WHERE purpose='performance')::int AS performance_total,
            count(*) FILTER (WHERE purpose='performance' AND status='completed')::int AS performance_completed
            FROM experiments WHERE purpose IN ('attack','normal','performance')''').fetchone()
        models = conn.execute('''SELECT count(DISTINCT c.requested_model)::int AS total
            FROM experiment_cases c JOIN experiments e ON e.id=c.experiment_id
            WHERE e.purpose='performance' ''').fetchone()['total']
        recent = recent_experiments(conn)
        performance_recent = recent_experiments(conn, performance_only=True)
    return {
        'generatedAt': datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
        'datasets': {'normal':normal,'attacks':attacks['total'],'direct':attacks['direct'],
                     'indirect':attacks['indirect'],'categories':categories,'groundTruth':ground_truth},
        'experiments': {'total':counts['total'],'active':counts['active'],'recent':recent},
        'performance': {'total':counts['performance_total'],'completed':counts['performance_completed'],
                        'modelCount':models,'recent':performance_recent},
    }
