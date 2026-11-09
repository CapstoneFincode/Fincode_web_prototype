"""Performance integration checks against a disposable PostgreSQL DB.

FINCODE_TEST_DATABASE_URL is required. Model HTTP is always mocked; this suite
clears experiment records and its own PERF-TEST-* dataset fixtures only.
"""
import asyncio
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from backend.app import create_app
from backend.database import Database
from backend.migrate import migrate


URL=os.environ.get('FINCODE_TEST_DATABASE_URL')
DATASET_ID='ground_truth_set/v1'
SOURCES=json.loads((Path(__file__).resolve().parents[2]/'datasets/ground_truth_set/v1/emails.json').read_text())


@unittest.skipUnless(URL,'Set FINCODE_TEST_DATABASE_URL to a disposable PostgreSQL DB')
class PerformanceTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        migrate(URL)
        cls.db=Database(URL,actor='performance-test')
        cls.db.seed()

    async def asyncSetUp(self):
        with self.db.connect() as conn:
            conn.execute('TRUNCATE experiments CASCADE')
            conn.execute("DELETE FROM ground_truths WHERE email_case_id IN (SELECT id FROM email_cases WHERE case_key LIKE 'PERF-TEST-%')")
            conn.execute("DELETE FROM email_cases WHERE case_key LIKE 'PERF-TEST-%'")
            for source in SOURCES:
                self.db._save(conn,'normal',{**source,'reviewStatus':'reviewed'},'PERF-TEST-'+source['id'])
            conn.execute("""UPDATE ground_truths SET review_status='reviewed',reviewed_by='performance-test',
                reviewed_at=now(),review_note='Disposable fixture approved for testing'
                WHERE email_case_id IN (SELECT id FROM email_cases WHERE case_key LIKE 'PERF-TEST-%')""")
        self.rows={row['id'].removeprefix('PERF-TEST-'):row for row in self.db.datasets()['normal'] if row['id'].startswith('PERF-TEST-')}
        self.calls=[]
        self.statuses={}
        self.omit_usage=set()
        self.output={}
        self.block_next=False
        self.request_started=asyncio.Event()
        self.release_request=asyncio.Event()

        async def model(request):
            if request.method=='GET':
                return httpx.Response(200,json={'data':[{'id':f'model-{letter}'} for letter in 'abcde']})
            payload=json.loads(request.content)
            self.calls.append(payload)
            if self.block_next:
                self.block_next=False
                self.request_started.set()
                await self.release_request.wait()
            selected=payload['model']
            status=self.statuses.get(selected,[])
            code=status.pop(0) if status else 200
            if code!=200:
                return httpx.Response(code,json={'error':{'message':'mock upstream failure'}})
            source=next(source for source in SOURCES if source['body'] in payload['messages'][-1]['content'])
            response={'model':selected+'-resolved','id':'mock-'+str(len(self.calls)),
                'choices':[{'message':{'content':self.output.get(selected,source['reference'])},'finish_reason':'stop'}]}
            if selected not in self.omit_usage:
                response['usage']={'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}
            return httpx.Response(200,json=response)

        self.app=create_app(database_url=URL,api_key='test-key-not-real',run_worker=False,
                            upstream_transport=httpx.MockTransport(model))
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://testserver')

    async def asyncTearDown(self):
        self.release_request.set()
        await self.client.aclose()

    def payload(self,**changes):
        return {'name':'Performance integration','datasetId':DATASET_ID,
            'emailIds':[self.rows[source['id']]['dbId'] for source in SOURCES[:2]],
            'models':['model-a','model-b'],'defense':'off','idempotencyKey':str(uuid4()),**changes}

    async def start(self,**changes):
        response=await self.client.post('/api/performance',json=self.payload(**changes))
        self.assertEqual(response.status_code,202,response.text)
        return response.json()['id']

    async def summary(self,identifier):
        response=await self.client.get('/api/performance/'+identifier)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    async def cases(self,identifier,offset=0,limit=20):
        response=await self.client.get(f'/api/performance/{identifier}/cases',params={'offset':offset,'limit':limit})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    async def drain(self,maximum=30):
        for _ in range(maximum):
            if not await self.app.state.worker_once():
                return
        self.fail('Mock worker did not exhaust its bounded queue')

    def result_for(self,summary,model):
        return next(row for row in summary['modelResults'] if row['model']==model)

    async def test_dataset_is_exact_source_set_and_excludes_sample_rows(self):
        response=await self.client.get('/api/performance/dataset')
        self.assertEqual(response.status_code,200,response.text)
        dataset=response.json()['dataset']
        self.assertEqual(dataset['id'],DATASET_ID)
        self.assertEqual(dataset['total'],100)
        self.assertEqual(dataset['available'],100)
        self.assertEqual(dataset['reviewedLabels'],100)
        self.assertEqual(dataset['reviewedReferences'],100)
        self.assertEqual({row['sourceId'] for row in dataset['items']},{row['id'] for row in SOURCES})
        self.assertEqual({row['id'] for row in dataset['items']},{row['dbId'] for row in self.rows.values()})
        self.assertEqual(len(dataset['items']),100)
        self.assertEqual(self.calls,[])

    async def test_ambiguous_duplicate_source_matching_is_rejected(self):
        with self.db.connect() as conn:
            self.db._save(conn,'normal',{**SOURCES[0],'reviewStatus':'reviewed'},'PERF-TEST-DUPLICATE')
        response=await self.client.get('/api/performance/dataset')
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(response.json()['error']['code'],'DATASET_AMBIGUOUS')
        response=await self.client.post('/api/performance',json=self.payload())
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(response.json()['error']['code'],'DATASET_AMBIGUOUS')
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM experiments').fetchone()['n'],0)
        self.assertEqual(self.calls,[])

    async def test_request_validation_prevents_invalid_or_unbounded_calls(self):
        first=self.rows[SOURCES[0]['id']]['dbId']
        sample=next(row for row in self.db.datasets()['normal'] if row['provenance']=='sample')
        invalid=[{'models':[]},{'models':['model-a','model-a']},{'models':[f'model-{n}' for n in range(6)]},
            {'models':['']},{'models':['codex-model']},{'emailIds':[]},{'emailIds':[first,first]},
            {'emailIds':[str(uuid4()) for _ in range(101)]},{'emailIds':[sample['dbId']]},
            {'emailIds':[str(uuid4())]},{'datasetId':'other-dataset'},{'defense':'filter'},{'repeat':2}]
        for change in invalid:
            with self.subTest(change=change):
                response=await self.client.post('/api/performance',json=self.payload(**change))
                self.assertIn(response.status_code,(400,409,422),response.text)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM experiments').fetchone()['n'],0)
        self.assertEqual(self.calls,[])

    async def test_dataset_uses_only_latest_unarchived_matching_input(self):
        row=self.rows[SOURCES[0]['id']]
        changed=await self.client.put('/api/data/normal/'+row['id'],json={**row,'body':'원본 합성 데이터와 다른 본문'})
        self.assertEqual(changed.status_code,200,changed.text)
        archived=self.rows[SOURCES[1]['id']]
        deleted=await self.client.request('DELETE','/api/data/normal/'+archived['id'],json={'dbVersion':archived['dbVersion']})
        self.assertEqual(deleted.status_code,200,deleted.text)
        response=await self.client.get('/api/performance/dataset')
        self.assertEqual(response.status_code,200,response.text)
        dataset=response.json()['dataset']
        self.assertEqual(dataset['available'],98)
        self.assertFalse({SOURCES[0]['id'],SOURCES[1]['id']} & {item['sourceId'] for item in dataset['items']})
        for stale in (row,archived):
            response=await self.client.post('/api/performance',json=self.payload(emailIds=[stale['dbId']],models=['model-a']))
            self.assertIn(response.status_code,(400,409),response.text)
        self.assertEqual(self.calls,[])

    async def test_500_case_creation_is_atomic_and_idempotent_without_model_calls(self):
        payload=self.payload(models=[f'model-{letter}' for letter in 'abcde'],
            emailIds=[self.rows[source['id']]['dbId'] for source in SOURCES])
        created=await self.client.post('/api/performance',json=payload)
        self.assertEqual(created.status_code,202,created.text)
        identifier=created.json()['id']
        again=await self.client.post('/api/performance',json=payload)
        self.assertEqual(again.status_code,202,again.text)
        self.assertEqual(again.json()['id'],identifier)
        conflicting=await self.client.post('/api/performance',json={**payload,'models':['model-a']})
        self.assertEqual(conflicting.status_code,409,conflicting.text)
        with self.db.connect() as conn:
            experiment=conn.execute('SELECT * FROM experiments WHERE id=%s',(identifier,)).fetchone()
            counts=conn.execute('''SELECT count(*) AS total,count(DISTINCT case_number) AS numbers,
                count(DISTINCT requested_model) AS models,count(DISTINCT email_case_id) AS emails,
                bool_and(ground_truth_id IS NOT NULL AND repeat_index=1) AS frozen
                FROM experiment_cases WHERE experiment_id=%s''',(identifier,)).fetchone()
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM experiments').fetchone()['n'],1)
        self.assertEqual(experiment['purpose'],'performance')
        self.assertEqual(counts,{'total':500,'numbers':500,'models':5,'emails':100,'frozen':True})
        summary=await self.summary(identifier)
        self.assertEqual(summary['total'],500)
        self.assertEqual(summary['queued'],500)
        self.assertEqual(summary['finished'],0)
        self.assertFalse(summary['preliminary'])
        full=await self.client.post('/api/performance',json=self.payload())
        self.assertEqual(full.status_code,409,full.text)
        self.assertEqual(full.json()['error']['code'],'PERFORMANCE_QUEUE_FULL')
        cancelled=await self.client.post('/api/performance/'+identifier+'/cancel',json={})
        self.assertEqual(cancelled.status_code,200,cancelled.text)
        new=await self.client.post('/api/performance',json=self.payload())
        self.assertEqual(new.status_code,202,new.text)
        self.assertEqual(self.calls,[])

    async def test_unreviewed_labels_rejected_but_draft_reference_marked_preliminary(self):
        row=self.rows[SOURCES[0]['id']]
        with self.db.connect() as conn:
            conn.execute("UPDATE email_cases SET label_review_status='pending',label_reviewed_by=NULL,label_reviewed_at=NULL WHERE id=%s",(row['dbId'],))
        response=await self.client.post('/api/performance',json=self.payload(emailIds=[row['dbId']],models=['model-a']))
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(response.json()['error']['code'],'NORMAL_REVIEW_REQUIRED')
        with self.db.connect() as conn:
            conn.execute("UPDATE email_cases SET label_review_status='reviewed',label_reviewed_by='performance-test',label_reviewed_at=now() WHERE id=%s",(row['dbId'],))
            conn.execute("UPDATE ground_truths SET review_status='draft' WHERE id=%s",(row['groundTruthId'],))
        identifier=await self.start(emailIds=[row['dbId']],models=['model-a'])
        self.assertTrue((await self.summary(identifier))['preliminary'])
        with self.db.connect() as conn:
            conn.execute("UPDATE ground_truths SET review_status='rejected' WHERE id=%s",(row['groundTruthId'],))
        response=await self.client.post('/api/performance',json=self.payload(emailIds=[row['dbId']],models=['model-a']))
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(response.json()['error']['code'],'REFERENCE_REQUIRED')
        missing=self.rows[SOURCES[1]['id']]
        with self.db.connect() as conn:
            conn.execute('DELETE FROM ground_truths WHERE id=%s',(missing['groundTruthId'],))
        response=await self.client.post('/api/performance',json=self.payload(emailIds=[missing['dbId']],models=['model-a']))
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(response.json()['error']['code'],'REFERENCE_REQUIRED')
        self.assertEqual(self.calls,[])

    async def test_input_and_reference_snapshots_survive_edits_and_reference_is_never_sent(self):
        source=SOURCES[0]
        row=self.rows[source['id']]
        identifier=await self.start(emailIds=[row['dbId']],models=['model-a'])
        changed=await self.client.put('/api/data/normal/'+row['id'],json={**row,
            'reference':'실험 생성 후 변경된 기준 정답 PRIVATE_REFERENCE_CHANGE','requiredFacts':['변경된 핵심 정보']})
        self.assertEqual(changed.status_code,200,changed.text)
        await self.app.state.worker_once()
        self.assertEqual(len(self.calls),1)
        messages=self.calls[0]['messages']
        self.assertEqual(messages[-1],{'role':'user','content':f"제목: {source['title']}\n\n{source['body']}"})
        self.assertEqual(len(messages),2)
        self.assertNotIn('PRIVATE_REFERENCE_CHANGE',json.dumps(messages,ensure_ascii=False))
        case=(await self.cases(identifier))['cases'][0]
        self.assertEqual(case['sourceId'],source['id'])
        self.assertEqual(case['emailId'],row['dbId'])
        self.assertEqual(case['reference'],source['reference'])
        self.assertEqual(case['body'],source['body'])
        self.assertEqual(case['referenceReviewStatus'],'reviewed')
        self.assertEqual(case['quality']['score'],1)
        self.assertTrue(case['quality']['version'])
        self.assertEqual(case['requestedModel'],'model-a')
        self.assertEqual(case['returnedModel'],'model-a-resolved')

    async def test_comparison_uses_common_completed_emails_and_preserves_unknown_usage(self):
        self.omit_usage.add('model-b')
        identifier=await self.start()
        for _ in range(3):
            await self.app.state.worker_once()
        partial=await self.summary(identifier)
        self.assertEqual(partial['finished'],3)
        self.assertEqual(partial['commonCompletedEmails'],1)
        self.assertEqual([call['model'] for call in self.calls],['model-a','model-b','model-b'])
        await self.drain()
        summary=await self.summary(identifier)
        self.assertEqual(summary['status'],'completed')
        self.assertEqual(summary['succeeded'],4)
        self.assertEqual(summary['commonCompletedEmails'],2)
        for model in ('model-a','model-b'):
            result=self.result_for(summary,model)
            self.assertEqual(result['meanRougeL'],1)
            self.assertEqual(result['common']['count'],2)
            self.assertEqual(result['common']['meanRougeL'],1)
        known=self.result_for(summary,'model-a')
        unknown=self.result_for(summary,'model-b')
        self.assertEqual(known['tokens'],{'inputMean':10,'outputMean':20,'totalMean':30,'reportedCount':2})
        self.assertEqual(unknown['tokens'],{'inputMean':None,'outputMean':None,'totalMean':None,'reportedCount':0})
        self.assertIsNone(unknown['common']['totalTokensMean'])
        cases=(await self.cases(identifier))['cases']
        for case in cases:
            self.assertIsNone(case['error'])
            if case['requestedModel']=='model-b':
                self.assertIsNone(case['usage']['totalTokens'])
        listing=await self.client.get('/api/performance')
        self.assertEqual(listing.status_code,200,listing.text)
        self.assertIn(identifier,[row['id'] for row in listing.json()['evaluations']])

    async def test_execution_failures_are_excluded_from_quality_means_and_common_sample(self):
        self.statuses['model-a']=[500]
        identifier=await self.start()
        await self.drain()
        summary=await self.summary(identifier)
        self.assertEqual(summary['status'],'partial')
        self.assertEqual(summary['failed'],1)
        self.assertEqual(summary['succeeded'],3)
        self.assertEqual(summary['commonCompletedEmails'],1)
        self.assertEqual(self.result_for(summary,'model-a')['meanRougeL'],1)
        failed=next(case for case in (await self.cases(identifier))['cases'] if case['status']=='failed')
        self.assertIsNone(failed['quality'])
        self.assertTrue(failed['error']['code'])
        self.assertEqual(len(self.calls),4)

    async def test_scoring_error_preserves_model_output_without_inventing_zero_quality(self):
        row=self.rows[SOURCES[0]['id']]
        identifier=await self.start(models=['model-a'],emailIds=[row['dbId']])
        with patch('backend.summary_quality.evaluate_summary',side_effect=RuntimeError('mock scoring failure')):
            await self.app.state.worker_once()
        summary=await self.summary(identifier)
        self.assertEqual(summary['succeeded'],1)
        self.assertEqual(summary['failed'],0)
        self.assertEqual(summary['commonCompletedEmails'],0)
        result=self.result_for(summary,'model-a')
        self.assertEqual(result['gradedCount'],0)
        self.assertEqual(result['totalFacts'],len(SOURCES[0]['requiredFacts']))
        self.assertEqual(result['needsReviewFacts'],len(SOURCES[0]['requiredFacts']))
        self.assertIsNone(result['meanRougeL'])
        self.assertIsNone(result['meanFactCoverage'])
        case=(await self.cases(identifier))['cases'][0]
        self.assertEqual(case['status'],'succeeded')
        self.assertEqual(case['summary'],SOURCES[0]['reference'])
        self.assertIsNone(case['quality']['score'])
        self.assertEqual(case['quality']['decisionStatus'],'indeterminate')
        self.assertEqual(case['quality']['errorCode'],'SCORING_UNAVAILABLE')
        self.assertEqual(len(self.calls),1)

    async def test_cancel_only_queued_runs_and_preserve_in_flight_completion(self):
        identifier=await self.start()
        self.block_next=True
        worker=asyncio.create_task(self.app.state.worker_once())
        try:
            await asyncio.wait_for(self.request_started.wait(),5)
            cancelled=await self.client.post('/api/performance/'+identifier+'/cancel',json={})
            self.assertEqual(cancelled.status_code,200,cancelled.text)
            current=await self.summary(identifier)
            self.assertEqual(current['cancelled'],3)
            self.assertEqual(current['running'],1)
            self.assertEqual(current['queued'],0)
            with self.db.connect() as conn:
                queued_cancelled=conn.execute('''SELECT r.started_at,r.completed_at FROM runs r
                    JOIN experiment_cases c ON c.id=r.experiment_case_id
                    WHERE c.experiment_id=%s AND r.status='cancelled' ''',(identifier,)).fetchall()
            self.assertEqual(len(queued_cancelled),3)
            self.assertTrue(all(row['started_at'] is None and row['completed_at'] is not None for row in queued_cancelled))
        finally:
            self.release_request.set()
            await asyncio.wait_for(worker,5)
        final=await self.summary(identifier)
        self.assertEqual(final['succeeded'],1)
        self.assertEqual(final['cancelled'],3)
        self.assertEqual(final['finished'],4)
        self.assertEqual(final['running'],0)
        self.assertIsNotNone(next(case for case in (await self.cases(identifier))['cases'] if case['status']=='succeeded')['quality'])
        again=await self.client.post('/api/performance/'+identifier+'/cancel',json={})
        self.assertEqual(again.status_code,200,again.text)
        self.assertEqual(len(self.calls),1)
        self.assertFalse(await self.app.state.worker_once())

    async def test_authentication_failure_stops_all_queued_calls_without_retry(self):
        self.statuses['model-a']=[401]
        identifier=await self.start()
        await self.drain()
        summary=await self.summary(identifier)
        self.assertEqual(summary['failed'],1)
        self.assertEqual(summary['cancelled'],3)
        self.assertEqual(summary['queued'],0)
        self.assertTrue(summary['stopReason'])
        self.assertEqual(len(self.calls),1)

    async def test_rate_limit_stops_only_affected_model_without_retry(self):
        self.statuses['model-a']=[429]
        identifier=await self.start()
        await self.drain()
        summary=await self.summary(identifier)
        self.assertEqual(summary['failed'],1)
        self.assertEqual(summary['cancelled'],1)
        self.assertEqual(self.result_for(summary,'model-b')['succeeded'],2)
        self.assertEqual([call['model'] for call in self.calls],['model-a','model-b','model-b'])

    async def test_three_consecutive_errors_stop_affected_model_and_keep_other_model_running(self):
        self.statuses['model-a']=[500,500,500]
        identifier=await self.start(emailIds=[self.rows[source['id']]['dbId'] for source in SOURCES[:5]])
        await self.drain()
        summary=await self.summary(identifier)
        failed=self.result_for(summary,'model-a')
        self.assertEqual(failed['failed'],3)
        self.assertEqual(failed['cancelled'],2)
        self.assertIsNone(failed['meanRougeL'])
        self.assertEqual(self.result_for(summary,'model-b')['succeeded'],5)
        self.assertEqual(summary['commonCompletedEmails'],0)
        self.assertEqual(len(self.calls),8)

    async def test_success_resets_consecutive_error_counter(self):
        self.statuses['model-a']=[500,500,200,500,500]
        identifier=await self.start(models=['model-a'],emailIds=[self.rows[source['id']]['dbId'] for source in SOURCES[:5]])
        await self.drain()
        summary=await self.summary(identifier)
        self.assertEqual(summary['failed'],4)
        self.assertEqual(summary['succeeded'],1)
        self.assertEqual(summary['cancelled'],0)
        self.assertEqual(len(self.calls),5)

    async def test_case_pagination_is_bounded_and_queue_yields_to_other_experiments(self):
        first=await self.start(models=['model-a'])
        second=await self.start(models=['model-b'],emailIds=[self.rows[SOURCES[0]['id']]['dbId']])
        await self.drain()
        self.assertEqual([call['model'] for call in self.calls],['model-a','model-b','model-a'])
        page=await self.cases(first,offset=1,limit=1)
        self.assertEqual(page['total'],2)
        self.assertEqual(page['offset'],1)
        self.assertEqual(page['limit'],1)
        self.assertEqual(len(page['cases']),1)
        self.assertEqual((await self.cases(first,offset=2,limit=1))['cases'],[])
        for params in ({'offset':-1},{'limit':0},{'limit':1001},{'offset':'wrong'}):
            response=await self.client.get('/api/performance/'+first+'/cases',params=params)
            self.assertIn(response.status_code,(400,422),response.text)
        self.assertEqual((await self.summary(second))['succeeded'],1)
