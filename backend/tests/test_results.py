"""Results reporting on disposable PostgreSQL with mocked model calls only."""
import json
import os
import unittest
from uuid import uuid4

import httpx
from backend.app import create_app
from backend.database import Database, insert
from backend.migrate import migrate


URL = os.environ.get('FINCODE_TEST_DATABASE_URL')
ATTACK = {'name':'Results fixture', 'vector':'direct', 'channel':'input', 'technique':'override',
          'goal':'marker', 'payload':'Return RESULTS_TEST', 'criterion':'marker',
          'expectedOutput':'RESULTS_TEST', 'evaluationMode':'exact'}


@unittest.skipUnless(URL, 'Set FINCODE_TEST_DATABASE_URL to a disposable PostgreSQL DB')
class ResultsTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        migrate(URL)
        cls.db = Database(URL, actor='results-test')
        cls.db.seed()

    async def asyncSetUp(self):
        with self.db.connect() as conn:
            conn.execute('TRUNCATE experiments CASCADE')
        self.calls = []
        def model(request):
            self.calls.append(json.loads(request.content))
            return httpx.Response(200, json={'model':'resolved-model', 'id':'results-call',
                'choices':[{'message':{'content':'RESULTS_TEST'}, 'finish_reason':'stop'}],
                'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12}})
        self.app = create_app(api_key='test-key',database_url=URL,run_worker=False,upstream_transport=httpx.MockTransport(model))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://testserver')
        self.attack = (await self.client.post('/api/data/attacks',json=ATTACK)).json()['attacks'][-1]

    async def asyncTearDown(self):
        await self.client.aclose()

    async def start(self, **changes):
        payload = {'name':'Result experiment', 'kind':'attack', 'model':'requested-model',
                   'scenarioIds':[self.attack['dbId']], 'repeat':1, 'defense':'off',
                   'idempotencyKey':str(uuid4()), **changes}
        response = await self.client.post('/api/experiments',json=payload)
        self.assertEqual(response.status_code,202,response.text)
        return response.json()['id']

    async def models(self, query=''):
        response = await self.client.get('/api/results/models'+query)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    async def test_pagination_reaches_records_beyond_100_and_clamps_empty_or_last_page(self):
        empty = (await self.client.get('/api/experiments?page=100&pageSize=5')).json()
        self.assertEqual((empty['page'],empty['total'],empty['totalPages'],empty['experiments']),(1,0,1,[]))
        with self.db.connect() as conn:
            for i in range(106):
                insert(conn,'experiments',{'id':str(uuid4()),'name':f'Page {i}',
                    'purpose':'performance' if i==0 else 'attack','status':'completed','evaluation_policy':{},
                    'execution_plan':{},'environment_snapshot':{},'created_by':'results-test'})
        legacy = (await self.client.get('/api/experiments')).json()
        self.assertEqual(set(legacy),{'experiments'})
        self.assertEqual(len(legacy['experiments']),100)
        identifiers = []
        for page in range(1,23):
            value = (await self.client.get(f'/api/experiments?page={page}&pageSize=5')).json()
            self.assertEqual((value['total'],value['totalPages'],value['page']),(106,22,page))
            self.assertLessEqual(len(value['experiments']),5)
            identifiers.extend(row['id'] for row in value['experiments'])
        self.assertEqual(len(set(identifiers)),106)
        last = (await self.client.get('/api/experiments?page=999999999999999999999&pageSize=5')).json()
        self.assertEqual((last['page'],len(last['experiments'])),(22,1))
        self.assertEqual(last['experiments'][0]['models'],[])
        self.assertEqual((await self.client.get('/api/experiments?purpose=performance')).json()['total'],1)
        self.assertEqual(self.calls,[])

    async def test_metrics_separate_execution_errors_and_latest_human_judgments(self):
        eid = await self.start(repeat=9)
        for _ in range(9):
            await self.app.state.worker_once()
        detail = self.db.detail(eid)
        runs = detail['runs']
        for index,value in [(1,False),(2,None)]:
            response = await self.client.post('/api/runs/'+str(runs[index]['id'])+'/evaluations',
                json={'metric':'attack_success','value':value,'evidence':'Human reviewed'})
            self.assertEqual(response.status_code,200,response.text)
        with self.db.connect() as conn:
            # Later automatic results cannot replace either human false or human unknown.
            conn.execute("UPDATE evaluations SET created_at=now()+interval '1 day' WHERE judge_type='rule'")
            conn.execute('DELETE FROM evaluations WHERE run_id=%s',(runs[3]['id'],))
            for index,status in [(4,'failed'),(5,'timed_out'),(6,'queued'),(7,'cancelled'),(8,'running')]:
                conn.execute('UPDATE runs SET status=%s WHERE id=%s',(status,runs[index]['id']))
            conn.execute('UPDATE llm_calls SET latency_ms=2000')
            conn.execute('UPDATE llm_calls SET input_tokens=0,output_tokens=0,total_tokens=0,latency_ms=0 WHERE run_id=%s',(runs[0]['id'],))
            conn.execute('UPDATE llm_calls SET total_tokens=NULL WHERE run_id=%s',(runs[1]['id'],))
            conn.execute('UPDATE runs SET call_log_complete=false WHERE id=%s',(runs[2]['id'],))
            conn.execute('DELETE FROM llm_calls WHERE run_id=%s',(runs[3]['id'],))
            for number,purpose,tokens,latency in [(2,'security',6,50),(3,'evaluator',9999,9999)]:
                insert(conn,'llm_calls',{'id':str(uuid4()),'run_id':runs[0]['id'],'call_number':number,
                    'purpose':purpose,'provider':'baze','requested_model':'judge-only','returned_model':'judge-only',
                    'status':'succeeded','request_snapshot':{},'input_tokens':4,'output_tokens':2,
                    'total_tokens':tokens,'latency_ms':latency})
        before = self.db.detail(eid)
        data = await self.models()
        self.assertEqual(self.db.detail(eid),before)  # GETs do not rewrite measurements or judgments.
        self.assertEqual((data['purpose'],data['vector'],data['defense']),('attack','all','off'))
        row = data['models'][0]
        self.assertEqual(row['model'],'requested-model')
        self.assertEqual(row['returnedModels'],['resolved-model'])
        self.assertEqual({k:row[k] for k in ['total','succeeded','errors','queued','running','cancelled']},
                         {'total':9,'succeeded':4,'errors':2,'queued':1,'running':1,'cancelled':1})
        self.assertEqual(row['attack'],{'successes':1,'failures':1,'indeterminate':1,'unjudged':1,'judged':2,'asr':50})
        self.assertEqual((row['latencyMsMean'],row['latencyReportedCount']),(1025,2))
        self.assertEqual(row['tokens'],{'inputMean':7,'outputMean':2,'totalMean':6,
                         'inputReportedCount':2,'outputReportedCount':2,'totalReportedCount':1})
        self.assertEqual(row['evaluatorVersionCounts'],{'exact-trim-v1':1,'manual-v1':2})
        self.assertEqual(row['inputModes'],['scenario-only-v1'])
        self.assertEqual(row['scenarioCount'],1)
        self.assertEqual(row['scenarioVersionCount'],1)
        self.assertIsNone(row['quality'])

    async def test_legacy_summaries_are_preserved_but_excluded_from_lists_and_counts(self):
        # Model-free fixture rows include legacy queued/running summaries: those
        # must neither consume page slots nor keep the results poller active.
        expected = set()
        summaries = set()
        with self.db.connect() as conn:
            for index,purpose in enumerate(['attack','summary','normal','summary','performance',
                                            'attack','summary','normal','performance']):
                identifier = str(uuid4())
                (summaries if purpose=='summary' else expected).add(identifier)
                insert(conn,'experiments',{'id':identifier,'name':f'Preserved {index}',
                    'purpose':purpose,'status':'running' if purpose=='summary' else 'completed',
                    'evaluation_policy':{},'execution_plan':{},'environment_snapshot':{},
                    'created_by':'results-test'})
            before = conn.execute('SELECT * FROM experiments ORDER BY id').fetchall()
        pages = [(await self.client.get(f'/api/experiments?page={page}&pageSize=5')).json()
                 for page in [1,2]]
        self.assertEqual([len(page['experiments']) for page in pages],[5,1])
        self.assertTrue(all((page['total'],page['totalPages'],page['activeCount'])==(6,2,0)
                            for page in pages))
        self.assertEqual({row['id'] for page in pages for row in page['experiments']},expected)
        explicit_all = (await self.client.get('/api/experiments?page=1&pageSize=100&purpose=all')).json()
        self.assertEqual({row['id'] for row in explicit_all['experiments']},expected)
        legacy = (await self.client.get('/api/experiments')).json()['experiments']
        self.assertEqual({row['id'] for row in legacy},expected)
        for purpose in ['attack','normal','performance']:
            filtered = (await self.client.get('/api/experiments?purpose='+purpose)).json()
            self.assertEqual(filtered['total'],2)
            self.assertTrue(all(row['purpose']==purpose for row in filtered['experiments']))
        for identifier in summaries:
            detail = await self.client.get('/api/experiments/'+identifier)
            self.assertEqual(detail.status_code,200)
            self.assertEqual(detail.json()['experiment']['purpose'],'summary')
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT * FROM experiments ORDER BY id').fetchall(),before)
        self.assertEqual(self.calls,[])

    async def test_frozen_vectors_archived_versions_and_unmeasured_pending_models(self):
        direct = await self.start()
        indirect = (await self.client.post('/api/data/attacks',json={**ATTACK,'vector':'indirect','channel':'email','name':'Indirect'})).json()['attacks'][-1]
        first = await self.start(scenarioIds=[indirect['dbId']])
        changed = (await self.client.put('/api/data/attacks/'+indirect['id'],json={**indirect,'name':'Indirect revision'})).json()
        revised = next(row for row in changed['attacks'] if row['id']==indirect['id'])
        second = await self.start(scenarioIds=[revised['dbId']])
        await self.client.request('DELETE','/api/data/attacks/'+revised['id'],json={'dbVersion':revised['dbVersion']})
        with self.db.connect() as conn:
            # Missing legacy vector snapshots use the frozen scenario FK, including archived versions.
            conn.execute("UPDATE experiment_cases SET input_snapshot=input_snapshot-'attackScenario' WHERE experiment_id=%s",(first,))
        all_rows = (await self.models())['models'][0]
        self.assertEqual((all_rows['total'],all_rows['scenarioCount'],all_rows['scenarioVersionCount']),(3,2,3))
        row = (await self.models('?vector=indirect'))['models'][0]
        self.assertEqual((row['total'],row['scenarioCount'],row['scenarioVersionCount']),(2,1,2))
        self.assertIsNone(row['attack']['asr'])
        self.assertIsNone(row['latencyMsMean'])
        self.assertIsNone(row['tokens']['totalMean'])
        self.assertEqual(row['returnedModels'],[])
        page = (await self.client.get('/api/experiments?page=1&pageSize=5&model=requested-model&purpose=attack&vector=indirect&provider=baze')).json()
        self.assertEqual({item['id'] for item in page['experiments']},{first,second})
        self.assertEqual(page['activeCount'],3)  # Includes the active direct experiment outside the filter.
        self.assertEqual(page['experiments'][0]['models'],['requested-model'])
        self.assertEqual((await self.client.get('/api/experiments?provider=other')).json()['total'],0)
        self.assertEqual(self.calls,[])

    async def test_purpose_provider_and_defense_cohorts_do_not_mix(self):
        ids = [await self.start() for _ in range(5)]
        for _ in ids:
            await self.app.state.worker_once()
        runs = [self.db.detail(eid)['runs'][0] for eid in ids]
        with self.db.connect() as conn:
            for eid,purpose in zip(ids[1:4],['normal','summary','performance']):
                conn.execute('UPDATE experiments SET purpose=%s WHERE id=%s',(purpose,eid))
                conn.execute("UPDATE experiment_cases SET expected_label='normal',attack_scenario_id=NULL WHERE experiment_id=%s",(eid,))
            conn.execute('DELETE FROM evaluations WHERE run_id=%s',(runs[3]['id'],))
            insert(conn,'evaluations',{'id':str(uuid4()),'run_id':runs[3]['id'],'metric':'summary_quality','judge_type':'rule',
                'evaluator_version':'quality-test-v1','decision_status':'determinate','score':0.75,
                'rubric_snapshot':{},'evidence':'Similarity only','evaluated_by':'results-test'})
            conn.execute("UPDATE experiment_cases SET provider='other-provider' WHERE experiment_id=%s",(ids[4],))
            profile = str(uuid4())
            insert(conn,'defense_profiles',{'id':profile,'profile_key':profile,'revision':1,'name':'Future defense fixture',
                'implementation_status':'planned','ordered_steps':[{'name':'fixture'}],'created_by':'results-test'})
            insert(conn,'runs',{'id':str(uuid4()),'experiment_case_id':runs[0]['experiment_case_id'],
                'security_gateway_enabled':True,'defense_profile_id':profile,'idempotency_key':str(uuid4()),'status':'blocked'})
        attack = (await self.models())['models']
        self.assertEqual(len(attack),2)
        self.assertEqual({row['provider'] for row in attack},{'baze','other-provider'})
        self.assertTrue(all(row['total']==1 and row['blocked']==0 for row in attack))
        summary_before = self.db.detail(ids[2])
        for purpose in ['normal','performance']:
            row = (await self.models('?purpose='+purpose))['models'][0]
            self.assertEqual(row['total'],1)
            self.assertIsNone(row['attack'])
            if purpose=='performance':
                self.assertEqual(row['quality'],{'gradedCount':1,'meanRougeL':0.75})
                self.assertEqual(row['evaluatorVersions'],['quality-test-v1'])
            else:
                self.assertIsNone(row['quality'])
        self.assertEqual((await self.client.get('/api/results/models?purpose=summary')).status_code,400)
        self.assertEqual(self.db.detail(ids[2]),summary_before)

    async def test_invalid_query_values_are_rejected_without_model_calls(self):
        for url in ['/api/experiments?page=0','/api/experiments?pageSize=101','/api/experiments?pageSize=0',
                    '/api/experiments?purpose=invalid','/api/experiments?purpose=summary','/api/experiments?model=',
                    '/api/experiments?provider=','/api/experiments?purpose=summary&vector=direct',
                    '/api/results/models?purpose=all','/api/results/models?purpose=invalid','/api/results/models?purpose=summary',
                    '/api/results/models?vector=other','/api/results/models?purpose=normal&vector=indirect']:
            response = await self.client.get(url)
            self.assertEqual(response.status_code,400,(url,response.text))
        response = await self.client.get('/api/experiments?page=not-a-number')
        self.assertEqual(response.status_code,422)
        self.assertEqual(self.calls,[])
