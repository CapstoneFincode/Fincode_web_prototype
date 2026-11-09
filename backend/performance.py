"""Frozen normal-email evaluations using the existing durable experiment queue."""
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from statistics import mean, median
from uuid import uuid4

from psycopg.types.json import Jsonb

from backend.database import DataError, digest, insert, text, uuid


DATASET_ID='ground_truth_set/v1'
DATASET_NAME='합성 정상 이메일 100건 v1'
TERMINAL=('succeeded','failed','timed_out','cancelled','blocked')


@lru_cache(maxsize=1)
def source_dataset():
    directory=Path(__file__).resolve().parent.parent/'datasets/ground_truth_set/v1'
    try:
        raw=(directory/'emails.json').read_bytes()
        manifest=json.loads((directory/'manifest.json').read_text('utf-8'))
        rows=json.loads(raw)
        sha=hashlib.sha256(raw).hexdigest()
        if sha!=manifest['sha256'] or len(rows)!=100 or {r['id'] for r in rows}!={f'GT-SYN-{n:03d}' for n in range(1,101)}:
            raise ValueError('Invalid source dataset')
        fingerprints={digest({'title':row['title'],'body':row['body']}) for row in rows}
        if len(fingerprints)!=100:
            raise ValueError('Ambiguous source dataset')
        return rows,sha,manifest.get('version')
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise DataError('평가 데이터 원본을 확인할 수 없습니다. 서버의 데이터셋 배포 상태를 확인해 주세요.',503,'DATASET_UNAVAILABLE') from exc


def matched_dataset(conn):
    source,sha,version=source_dataset()
    current=conn.execute('''SELECT e.*,g.id AS ground_truth_id,g.revision AS ground_truth_revision,
        g.reference_summary,g.required_facts AS quality_facts,g.review_status AS reference_review_status
        FROM (SELECT DISTINCT ON (case_key) * FROM email_cases ORDER BY case_key,revision DESC) e
        LEFT JOIN LATERAL (SELECT * FROM ground_truths WHERE email_case_id=e.id ORDER BY revision DESC LIMIT 1) g ON true
        WHERE e.archived_at IS NULL AND e.provenance<>'sample' AND e.expected_label='normal'
          AND e.label_review_status<>'sample' ''').fetchall()
    by_content=defaultdict(list)
    for row in current:
        by_content[digest({'title':row['subject'],'body':row['body']})].append(row)
    matches=[]
    for original in source:
        candidates=by_content[digest({'title':original['title'],'body':original['body']})]
        if len(candidates)>1:
            raise DataError('합성 평가 원본과 같은 내용의 DB 항목이 여러 개 있습니다. 중복 자료를 정리해 주세요.',409,'DATASET_AMBIGUOUS')
        if candidates:
            matches.append((original,candidates[0]))
    return matches,sha,version


def dataset(db):
    with db.connect() as conn:
        matches,_,_=matched_dataset(conn)
    return {'dataset':{'id':DATASET_ID,'name':DATASET_NAME,'total':100,'available':len(matches),
        'reviewedLabels':sum(row['label_review_status']=='reviewed' for _,row in matches),
        'reviewedReferences':sum(row['reference_review_status']=='reviewed' for _,row in matches),
        'items':[{'id':str(row['id']),'sourceId':source['id'],'caseKey':row['case_key'],'title':row['subject'],
            'reviewStatus':row['label_review_status'],'referenceReviewStatus':row['reference_review_status'],
            'dbVersion':row['revision'],'groundTruthId':str(row['ground_truth_id']) if row['ground_truth_id'] else None}
            for source,row in matches]}}


def validate_submission(data):
    fields={'name','datasetId','emailIds','models','defense','idempotencyKey','repeat'}
    if not isinstance(data,dict) or set(data)-fields:
        raise DataError('평가 이름, 데이터셋, 이메일, 모델과 방어 OFF 설정을 확인해 주세요.')
    if data.get('datasetId')!=DATASET_ID:
        raise DataError('지원하는 합성 정상 이메일 데이터셋을 선택해 주세요.')
    if data.get('defense')!='off':
        raise DataError('이번 성능 평가는 방어 OFF만 지원합니다.',400,'DEFENSE_NOT_SUPPORTED')
    if type(data.get('repeat',1)) is not int or data.get('repeat',1)!=1:
        raise DataError('성능 평가는 이메일·모델 조합마다 한 번만 실행합니다.')
    models=data.get('models')
    if not isinstance(models,list) or not 1<=len(models)<=5:
        raise DataError('모델은 1~5개 선택해 주세요.')
    for model in models:
        text(model,'모델',200)
        if model!=model.strip() or 'codex' in model.lower():
            raise DataError('현재 요약 경로에서 지원하는 모델을 선택해 주세요.')
    if len(set(models))!=len(models):
        raise DataError('서로 다른 모델을 선택해 주세요.')
    ids=data.get('emailIds')
    if not isinstance(ids,list) or not 1<=len(ids)<=100:
        raise DataError('평가 이메일은 1~100개 선택해 주세요.')
    ids=[uuid(identifier) for identifier in ids]
    if len(set(ids))!=len(ids) or len(ids)*len(models)>500:
        raise DataError('서로 다른 이메일을 선택하고 전체 호출 수를 500회 이하로 설정해 주세요.')
    return {'name':text(data.get('name','정상 이메일 성능 평가'),'평가 이름',200),
            'datasetId':DATASET_ID,'emailIds':ids,'models':models,'defense':'off','repeat':1,
            'idempotencyKey':text(data.get('idempotencyKey'),'요청 중복 방지 키',150)}


def enqueue(db,data,config):
    from backend.summary_quality import VERSION
    data=validate_submission(data)
    key=data['idempotencyKey']
    fingerprint=digest(data)
    with db.connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(key,))
        prior=conn.execute('SELECT id,purpose,submission_sha256 FROM experiments WHERE submission_key=%s',(key,)).fetchone()
        if prior:
            if prior['purpose']!='performance' or prior['submission_sha256']!=fingerprint:
                raise DataError('같은 요청 키에 다른 입력이 사용되었습니다.',409,'IDEMPOTENCY_CONFLICT')
            return {'id':str(prior['id']),'existing':True}
        conn.execute('SELECT pg_advisory_xact_lock(20261109,1)')
        outstanding=conn.execute('''SELECT count(*) AS total FROM runs r
            JOIN experiment_cases c ON c.id=r.experiment_case_id JOIN experiments e ON e.id=c.experiment_id
            WHERE e.purpose='performance' AND r.status IN ('queued','running')''').fetchone()['total']
        if outstanding+len(data['emailIds'])*len(data['models'])>500:
            raise DataError('대기·실행 중인 성능 평가 요청은 전체 500회까지입니다. 기존 평가 완료 후 다시 시도해 주세요.',409,'PERFORMANCE_QUEUE_FULL')
        matches,_,_=matched_dataset(conn)
        selected=set(data['emailIds'])
        keys=sorted(row['case_key'] for _,row in matches if str(row['id']) in selected)
        for case_key in keys:
            conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('normal'+case_key,))
        matches,sha,version=matched_dataset(conn)
        chosen=[(source,row) for source,row in matches if str(row['id']) in selected]
        if len(chosen)!=len(selected):
            raise DataError('선택한 이메일이 현재 합성 평가 데이터셋과 일치하지 않습니다. 새로고침해 주세요.',409,'DATASET_VERSION_CONFLICT')
        for _,row in chosen:
            if row['label_review_status']!='reviewed':
                raise DataError('정상 라벨 승인 완료 이메일만 평가할 수 있습니다.',409,'NORMAL_REVIEW_REQUIRED')
            if not row['reference_summary'] or not row['reference_summary'].strip() or row['reference_review_status'] not in ('draft','reviewed'):
                raise DataError('사용 가능한 기준 정답이 없는 이메일입니다.',409,'REFERENCE_REQUIRED')
        eid=str(uuid4())
        now=datetime.now(timezone.utc)
        preliminary=any(row['reference_review_status']!='reviewed' for _,row in chosen)
        plan={'datasetId':DATASET_ID,'datasetSha256':sha,'datasetVersion':version,
              'models':data['models'],'emailIds':[str(row['id']) for _,row in chosen],
              'sourceIds':[source['id'] for source,_ in chosen],'defense':'off','repeat':1,
              'concurrency':1,'automaticRetries':0,'order':'email-first-rotating-model-v1',
              'preliminary':preliminary,'totalCalls':len(chosen)*len(data['models'])}
        insert(conn,'experiments',{'id':eid,'name':data['name'],'purpose':'performance','status':'queued',
            'evaluation_policy':{'metric':'summary_quality','version':VERSION,'referenceToModel':False,
                'qualityRequired':True,'referenceDraftAllowed':True},
            'execution_plan':plan,'environment_snapshot':{'runtime':'python-fastapi','promptVersion':config['version']},
            'created_by':db.actor,'submission_key':key,'submission_sha256':fingerprint,'started_at':now})
        number=0
        for email_index,(original,row) in enumerate(chosen):
            # Balance first/last model positions without changing the requested comparison order.
            shift=email_index%len(data['models'])
            model_order=data['models'][shift:]+data['models'][:shift]
            original_evidence=dict(zip(original.get('requiredFacts',[]),original.get('factEvidence',[])))
            facts=row['quality_facts'] or []
            performance={'datasetId':DATASET_ID,'datasetSha256':sha,'datasetVersion':version,'sourceId':original['id'],
                'emailId':str(row['id']),'caseKey':row['case_key'],'emailRevision':row['revision'],
                'groundTruthId':str(row['ground_truth_id']),'groundTruthVersion':row['ground_truth_revision'],
                'reference':row['reference_summary'],'requiredFacts':facts,
                'factEvidence':[original_evidence.get(fact) for fact in facts],
                'referenceReviewStatus':row['reference_review_status'],'normalReviewStatus':row['label_review_status'],
                'scorerVersion':VERSION}
            performance['contentSha256']=digest({'title':row['subject'],'body':row['body'],
                'reference':performance['reference'],'requiredFacts':facts})
            messages=[{'role':'system','content':config['systemPrompt']},
                      {'role':'user','content':f"제목: {row['subject']}\n\n{row['body']}"}]
            snapshot={'email':{'subject':row['subject'],'body':row['body']},'messages':messages,
                      'inputMode':'email-v1','injectionStrategy':'none','performance':performance}
            for model in model_order:
                number+=1
                cid,rid=str(uuid4()),str(uuid4())
                insert(conn,'experiment_cases',{'id':cid,'experiment_id':eid,'case_number':number,'repeat_index':1,
                    'expected_label':'normal','email_case_id':row['id'],'ground_truth_id':row['ground_truth_id'],
                    'provider':'baze','requested_model':model,'prompt_version':config['version'],
                    'system_prompt':config['systemPrompt'],'inference_settings':{'max_tokens':config['maxTokens'],'stream':False},
                    'input_snapshot':snapshot,'input_sha256':digest(snapshot),'credential_alias':'server-key-1'})
                insert(conn,'runs',{'id':rid,'experiment_case_id':cid,'security_gateway_enabled':False,
                    'idempotency_key':rid,'status':'queued','environment_snapshot':{'runtime':'python-fastapi'}})
    return {'id':eid,'existing':False}


def score_result(conn,case,rid,response):
    from backend.summary_quality import VERSION, evaluate_summary
    frozen=case['input_snapshot']['performance']
    version=frozen['scorerVersion']
    try:
        if version!=VERSION:
            raise ValueError('Frozen scorer version unavailable')
        evaluation=evaluate_summary(frozen['reference'],frozen['requiredFacts'],response,
            source_body=case['input_snapshot']['email']['body'],fact_evidence=frozen.get('factEvidence'))
    except Exception:
        evaluation={'evaluator_version':version,'decision_status':'indeterminate','score':None,'boolean_value':None,
            'evidence':'채점 결과를 확정할 수 없습니다. 저장된 모델 출력과 기준 정답을 검토해 주세요.',
            'details':{'errorCode':'SCORING_UNAVAILABLE','rougeL':None,'factCoverage':None,
                       'confirmedFacts':0,'totalFacts':len(frozen['requiredFacts']),
                       'needsReviewFacts':len(frozen['requiredFacts'])}}
    insert(conn,'evaluations',{'id':str(uuid4()),'run_id':rid,'metric':'summary_quality','judge_type':'rule',
        'rubric_snapshot':{'datasetId':frozen['datasetId'],'datasetSha256':frozen['datasetSha256'],
            'groundTruthId':frozen['groundTruthId'],'groundTruthVersion':frozen['groundTruthVersion'],
            'reference':frozen['reference'],'requiredFacts':frozen['requiredFacts'],
            'referenceReviewStatus':frozen['referenceReviewStatus'],'version':version},
        'evaluated_by':'rule:'+version,**evaluation})


def cancel_queued(conn,eid,reason,model=None,code='EVALUATION_CANCELLED'):
    rows=conn.execute('''UPDATE runs r SET status='cancelled',completed_at=now(),call_log_complete=true,
        error_code=%s,error_message=%s FROM experiment_cases c
        WHERE c.id=r.experiment_case_id AND c.experiment_id=%s AND r.status='queued'
          AND (%s::text IS NULL OR c.requested_model=%s) RETURNING r.id''',(code,reason,eid,model,model)).fetchall()
    if rows:
        conn.execute('UPDATE experiments SET stop_reason=%s WHERE id=%s',(reason,eid))
    return len(rows)


def stop_after_failure(conn,case,status,payload):
    if status not in ('failed','timed_out') or 'performance' not in case['input_snapshot']:
        return
    eid=case['experiment_id']
    error=(payload.get('error') or {}).get('code')
    upstream=payload.get('upstreamStatus')
    if error=='GATEWAY_AUTH_ERROR' or upstream in (401,403):
        cancel_queued(conn,eid,'제공자 인증 오류로 남은 평가 요청을 중단했습니다.',code='AUTH_STOP')
        return
    if error=='GATEWAY_RATE_LIMIT' or upstream==429:
        cancel_queued(conn,eid,'요청 한도 오류로 해당 모델의 남은 평가를 중단했습니다.',case['requested_model'],'RATE_LIMIT_STOP')
        return
    recent=conn.execute('''SELECT r.status FROM runs r JOIN experiment_cases c ON c.id=r.experiment_case_id
        WHERE c.experiment_id=%s AND c.requested_model=%s AND r.status IN ('succeeded','failed','timed_out','blocked')
        ORDER BY r.completed_at DESC,r.id DESC LIMIT 3''',(eid,case['requested_model'])).fetchall()
    if len(recent)==3 and all(row['status'] in ('failed','timed_out') for row in recent):
        cancel_queued(conn,eid,'연속 3회 실행 오류로 해당 모델의 남은 평가를 중단했습니다.',case['requested_model'],'CONSECUTIVE_ERROR_STOP')


def experiment(conn,eid):
    row=conn.execute("SELECT * FROM experiments WHERE id=%s AND purpose='performance'",(uuid(eid),)).fetchone()
    if not row:
        raise DataError('성능 평가 기록을 찾을 수 없습니다.',404,'EVALUATION_NOT_FOUND')
    return row


def result_rows(conn,ids):
    # No bodies, summaries, provider payloads or per-case queries in progress polling.
    return conn.execute('''SELECT c.experiment_id,c.email_case_id,c.requested_model,r.status,call.latency_ms,
        call.input_tokens,call.output_tokens,call.total_tokens,
        v.score,v.decision_status,v.details
        FROM experiment_cases c JOIN runs r ON r.experiment_case_id=c.id
        LEFT JOIN llm_calls call ON call.run_id=r.id AND call.call_number=1
        LEFT JOIN LATERAL (SELECT score,decision_status,
            jsonb_build_object('rougeL',details->'rougeL','factCoverage',details->'factCoverage',
                'confirmedFacts',details->'confirmedFacts','totalFacts',details->'totalFacts',
                'needsReviewFacts',details->'needsReviewFacts') AS details
            FROM evaluations WHERE run_id=r.id AND metric='summary_quality' ORDER BY created_at DESC,id DESC LIMIT 1) v ON true
        WHERE c.experiment_id=ANY(%s::uuid[])''',(ids,)).fetchall()


def average(values):
    values=[float(value) for value in values if value is not None]
    return mean(values) if values else None


def summary(record,rows):
    models=record['execution_plan']['models']
    by_model={model:[row for row in rows if row['requested_model']==model] for model in models}
    successes={model:{str(row['email_case_id']) for row in selected
        if row['status']=='succeeded' and row['decision_status']=='determinate' and row['score'] is not None}
        for model,selected in by_model.items()}
    common=set.intersection(*successes.values()) if successes else set()
    def counts(selected):
        return {'total':len(selected),'finished':sum(row['status'] in TERMINAL for row in selected),
                'succeeded':sum(row['status']=='succeeded' for row in selected),
                'failed':sum(row['status'] in ('failed','timed_out','blocked') for row in selected),
                'cancelled':sum(row['status']=='cancelled' for row in selected),
                'running':sum(row['status']=='running' for row in selected),
                'queued':sum(row['status']=='queued' for row in selected)}
    def quality_values(selected,field):
        result=[]
        for row in selected:
            if row['status']!='succeeded' or row['decision_status']!='determinate' or row['score'] is None:
                continue
            details=row['details'] or {}
            value=(details.get('rougeL') or {}).get('f1') if field=='rougeL' else details.get(field)
            if value is not None:
                result.append(value)
        return result
    model_results=[]
    for model,selected in by_model.items():
        completed=[row for row in selected if row['status']=='succeeded']
        paired=[row for row in completed if str(row['email_case_id']) in common]
        latencies=[row['latency_ms'] for row in completed if row['latency_ms'] is not None]
        model_results.append({'model':model,**counts(selected),
            'gradedCount':sum(row['decision_status']=='determinate' and row['score'] is not None for row in completed),
            'meanRougeL':average(quality_values(completed,'rougeL')),
            'meanFactCoverage':average(quality_values(completed,'factCoverage')),
            'confirmedFacts':sum((row['details'] or {}).get('confirmedFacts') or 0 for row in completed),
            'totalFacts':sum((row['details'] or {}).get('totalFacts') or 0 for row in completed),
            'needsReviewFacts':sum((row['details'] or {}).get('needsReviewFacts') or 0 for row in completed),
            'latencyMsMean':average(latencies),'latencyMsMedian':median(latencies) if latencies else None,
            'tokens':{'inputMean':average(row['input_tokens'] for row in completed),
                      'outputMean':average(row['output_tokens'] for row in completed),
                      'totalMean':average(row['total_tokens'] for row in completed),
                      'reportedCount':sum(row['total_tokens'] is not None for row in completed)},
            'common':{'count':len(paired),'meanRougeL':average(quality_values(paired,'rougeL')),
                      'latencyMsMean':average(row['latency_ms'] for row in paired),
                      'totalTokensMean':average(row['total_tokens'] for row in paired)}})
    return {'id':str(record['id']),'name':record['name'],'status':record['status'],'datasetId':DATASET_ID,
        'createdAt':record['created_at'],'models':models,'defense':'off',**counts(rows),
        'preliminary':record['execution_plan'].get('preliminary',True),
        'commonCompletedEmails':len(common),'modelResults':model_results,'stopReason':record['stop_reason']}


def listing(db):
    with db.connect() as conn:
        records=conn.execute("SELECT * FROM experiments WHERE purpose='performance' ORDER BY created_at DESC,id DESC LIMIT 50").fetchall()
        rows=result_rows(conn,[record['id'] for record in records]) if records else []
    grouped=defaultdict(list)
    for row in rows:
        grouped[row['experiment_id']].append(row)
    return {'evaluations':[summary(record,grouped[record['id']]) for record in records]}


def detail(db,eid):
    with db.connect() as conn:
        record=experiment(conn,eid)
        rows=result_rows(conn,[record['id']])
    return summary(record,rows)


def cases(db,eid,offset=0,limit=20):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=50:
        raise DataError('결과 조회 범위는 offset 0 이상, limit 1~50으로 설정해 주세요.')
    with db.connect() as conn:
        record=experiment(conn,eid)
        total=conn.execute('SELECT count(*) AS total FROM experiment_cases WHERE experiment_id=%s',(record['id'],)).fetchone()['total']
        rows=conn.execute('''SELECT r.id,r.status,r.final_output,r.error_code,r.error_message,call.latency_ms,
            c.requested_model,c.input_snapshot,call.returned_model,call.input_tokens,call.output_tokens,call.total_tokens,
            call.finish_reason,r.response_payload->'warnings' AS warnings,
            v.evaluator_version,v.score,v.decision_status,v.evidence,v.details
            FROM experiment_cases c JOIN runs r ON r.experiment_case_id=c.id
            LEFT JOIN llm_calls call ON call.run_id=r.id AND call.call_number=1
            LEFT JOIN LATERAL (SELECT * FROM evaluations WHERE run_id=r.id AND metric='summary_quality'
                ORDER BY created_at DESC,id DESC LIMIT 1) v ON true
            WHERE c.experiment_id=%s ORDER BY c.case_number LIMIT %s OFFSET %s''',(record['id'],limit,offset)).fetchall()
    output=[]
    for row in rows:
        snapshot=row['input_snapshot']
        frozen=snapshot['performance']
        quality={**(row['details'] or {}),'score':float(row['score']) if row['score'] is not None else None,
            'version':row['evaluator_version'],'decisionStatus':row['decision_status'],'evidence':row['evidence']} if row['evaluator_version'] else None
        output.append({'id':str(row['id']),'sourceId':frozen['sourceId'],'title':snapshot['email']['subject'],
            'emailId':frozen['emailId'],'requestedModel':row['requested_model'],'returnedModel':row['returned_model'],
            'status':row['status'],'summary':row['final_output'],'reference':frozen['reference'],'body':snapshot['email']['body'],
            'finishReason':row['finish_reason'],'warnings':row['warnings'] if isinstance(row['warnings'],list) else [],
            'usage':{'inputTokens':row['input_tokens'],'outputTokens':row['output_tokens'],'totalTokens':row['total_tokens']},
            'latencyMs':row['latency_ms'],
            'error':{'code':row['error_code'],'message':row['error_message']} if row['error_code'] else None,
            'quality':quality,'referenceReviewStatus':frozen['referenceReviewStatus']})
    return {'total':total,'offset':offset,'limit':limit,'cases':output}


def cancel(db,eid):
    with db.connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(20261109,1)')
        record=experiment(conn,eid)
        cancel_queued(conn,record['id'],'사용자가 대기 중인 평가 요청을 취소했습니다.')
        db._refresh_status(conn)
    return detail(db,eid)
