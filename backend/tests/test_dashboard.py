"""Dashboard reads against disposable PostgreSQL; upstream model calls forbidden."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

import httpx
import psycopg

from backend.app import create_app
from backend.database import Database, insert
from backend.migrate import migrate
from backend.performance import source_dataset


URL = os.environ.get('FINCODE_TEST_DATABASE_URL')
ATTACK = {'name':'Dashboard attack','categoryId':'instruction-override','vector':'direct','channel':'input','technique':'override',
          'goal':'marker','payload':'PRIVATE_DASHBOARD_PAYLOAD','criterion':'marker',
          'expectedOutput':'TEST_MARKER','evaluationMode':'exact'}


class DashboardBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_database_is_not_an_empty_success(self):
        app = create_app(database_url='', api_key='', run_worker=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            response = await client.get('/api/dashboard')
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()['error']['code'],'DATABASE_NOT_CONFIGURED')
        self.assertEqual(response.headers['cache-control'],'no-store')

    async def test_database_failures_are_sanitized_and_shared_auth_still_applies(self):
        app = create_app(database_url='postgresql://unused', api_key='', run_worker=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            with patch('backend.database.Database.connect',side_effect=psycopg.OperationalError('SECRET_DSN')):
                response = await client.get('/api/dashboard')
            self.assertEqual(response.status_code,503)
            self.assertEqual(response.json()['error']['code'],'DATABASE_UNAVAILABLE')
            self.assertNotIn('SECRET_DSN',response.text)
            with patch.dict(os.environ,{'BACKEND_SHARED_TOKEN':'test-shared-token'}):
                response = await client.get('/api/dashboard')
            self.assertEqual(response.status_code,401)


@unittest.skipUnless(URL,'Set FINCODE_TEST_DATABASE_URL to a disposable PostgreSQL DB')
class DashboardTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        migrate(URL)
        cls.db = Database(URL, actor='dashboard-test')

    async def asyncSetUp(self):
        # This suite is only enabled by the explicit disposable-test DB setting.
        with self.db.connect() as conn:
            conn.execute('TRUNCATE experiments,email_cases,attack_scenarios CASCADE')
        self.db.seed()
        self.calls = []

        def upstream(request):
            self.calls.append(str(request.url))
            raise AssertionError('Dashboard must never call a model or gateway')

        self.app = create_app(database_url=URL,api_key='test-key',run_worker=False,
                              upstream_transport=httpx.MockTransport(upstream))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://testserver')

    async def asyncTearDown(self):
        await self.client.aclose()
        self.assertEqual(self.calls,[])
        with self.db.connect() as conn:
            conn.execute("DELETE FROM attack_categories WHERE id='dashboard-added'")
            conn.execute("UPDATE attack_categories SET archived_at=NULL WHERE id='instruction-override'")

    async def dashboard(self):
        response = await self.client.get('/api/dashboard')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.headers['cache-control'],'no-store')
        return response.json()

    def add_source(self,index,key,*,reviewed=True,reference_reviewed=True):
        row = source_dataset()[0][index]
        with self.db.connect() as conn:
            self.db._save(conn,'normal',{**row,'reviewStatus':'reviewed' if reviewed else 'pending'},key)
            if reference_reviewed:
                conn.execute('''UPDATE ground_truths SET review_status='reviewed',reviewed_by='dashboard-test',reviewed_at=now()
                    WHERE email_case_id IN (SELECT id FROM email_cases WHERE case_key=%s)''',(key,))

    async def test_empty_reports_use_real_zeroes_and_do_not_include_sample_ground_truth(self):
        data = await self.dashboard()
        current = self.db.datasets()
        self.assertEqual(data['datasets']['normal'],len(current['normal']))
        self.assertEqual(data['datasets']['categories'],len(current['categories']))
        self.assertEqual([data['datasets'][key] for key in ['attacks','direct','indirect']],[0,0,0])
        self.assertEqual(data['datasets']['groundTruth'],{
            'id':'ground_truth_set/v1','total':100,'available':0,'ready':0,
            'reviewedLabels':0,'reviewedReferences':0,'ambiguous':0,'error':None})
        self.assertEqual(data['experiments'],{'total':0,'active':0,'recent':[]})
        self.assertEqual(data['performance'],{'total':0,'completed':0,'modelCount':0,'recent':[]})
        self.assertTrue(data['generatedAt'].endswith('Z'))

    async def test_counts_follow_latest_revisions_archiving_and_dynamic_categories(self):
        normal = {'title':'Private title','body':'PRIVATE_NORMAL_BODY','reviewStatus':'pending','reference':''}
        saved = self.db.save('normal',normal)
        email = next(row for row in saved['normal'] if row['title']==normal['title'])
        self.db.save('normal',{**normal,'body':'PRIVATE_NEW_BODY','dbVersion':email['dbVersion']},email['id'])
        saved = self.db.save('attacks',ATTACK)
        attack = saved['attacks'][-1]
        self.db.save('attacks',{**ATTACK,'vector':'indirect','channel':'email','dbVersion':attack['dbVersion']},attack['id'])
        current = self.db.datasets()
        data = await self.dashboard()
        self.assertEqual(data['datasets']['normal'],len(current['normal']))
        self.assertEqual([data['datasets'][key] for key in ['attacks','direct','indirect']],[1,0,1])
        with self.db.connect() as conn:
            conn.execute("INSERT INTO attack_categories(id,name,normalized_name,scope,sort_order,created_by) VALUES ('dashboard-added','새 카테고리','새 카테고리','both',999,'dashboard-test')")
        self.assertEqual((await self.dashboard())['datasets']['categories'],len(current['categories'])+1)
        with self.db.connect() as conn:
            conn.execute("UPDATE attack_categories SET archived_at=now() WHERE id IN ('dashboard-added','instruction-override')")
        # Retired empty categories disappear; a category still used by a current
        # scenario remains visible, exactly as in the data-management listing.
        self.assertEqual((await self.dashboard())['datasets']['categories'],len(self.db.datasets()['categories']))
        revised = self.db.datasets()['attacks'][-1]
        self.db.archive('attacks',revised['id'],revised['dbVersion'])
        data = await self.dashboard()
        self.assertEqual([data['datasets'][key] for key in ['attacks','direct','indirect']],[0,0,0])
        self.assertNotIn('PRIVATE_',json.dumps(data))

    async def test_ground_truth_matches_contents_and_counts_both_review_requirements(self):
        self.add_source(0,'DASH-READY')
        self.add_source(1,'DASH-LABEL-PENDING',reviewed=False)
        self.add_source(2,'DASH-REFERENCE-DRAFT',reference_reviewed=False)
        original = source_dataset()[0][3]
        self.db.save('normal',{**original,'body':original['body']+' altered','reviewStatus':'reviewed'})
        expected = (await self.client.get('/api/performance/dataset')).json()['dataset']
        value = (await self.dashboard())['datasets']['groundTruth']
        self.assertEqual((value['available'],value['ready'],value['reviewedLabels'],value['reviewedReferences']),(3,1,2,2))
        self.assertEqual(value['available'],expected['available'])
        self.assertEqual(value['reviewedLabels'],expected['reviewedLabels'])
        self.assertEqual(value['reviewedReferences'],expected['reviewedReferences'])
        self.assertNotIn(original['body'],json.dumps(value,ensure_ascii=False))

    async def test_duplicate_source_data_keeps_dashboard_readable_but_blocks_readiness(self):
        self.add_source(0,'DASH-DUPLICATE-1')
        self.add_source(0,'DASH-DUPLICATE-2')
        self.add_source(1,'DASH-UNIQUE')
        self.assertEqual((await self.client.get('/api/performance/dataset')).status_code,409)
        data = await self.dashboard()
        value = data['datasets']['groundTruth']
        self.assertEqual((value['available'],value['ready'],value['ambiguous']),(1,0,1))
        self.assertTrue(value['error'])
        self.assertEqual(data['experiments']['total'],0)

    async def test_recent_records_are_bounded_ordered_and_exclude_legacy_summaries_without_writes(self):
        now = datetime.now(timezone.utc)
        expected = []
        with self.db.connect() as conn:
            for index in range(9):
                identifier = str(uuid4())
                purpose = 'summary' if index==8 else 'performance' if index%2 else 'attack'
                status = 'running' if index in (6,8) else 'completed'
                if purpose!='summary':
                    expected.append(identifier)
                insert(conn,'experiments',{'id':identifier,'name':f'Dashboard {index}','purpose':purpose,
                    'status':status,'evaluation_policy':{},'execution_plan':{'defense':'filter','secret':'PRIVATE_PLAN'},
                    'environment_snapshot':{},'created_by':'dashboard-test','created_at':now+timedelta(seconds=index)})
                if purpose=='performance':
                    insert(conn,'experiment_cases',{'id':str(uuid4()),'experiment_id':identifier,'case_number':1,
                        'repeat_index':1,'expected_label':'normal','provider':'baze','requested_model':'model-a' if index<5 else 'model-b',
                        'prompt_version':'test','system_prompt':'PRIVATE_SYSTEM','inference_settings':{},
                        'input_snapshot':{'secret':'PRIVATE_INPUT'},'input_sha256':'a'*64})
            before = conn.execute('SELECT * FROM experiments ORDER BY id').fetchall()
        data = await self.dashboard()
        self.assertEqual((data['experiments']['total'],data['experiments']['active']),(8,1))
        self.assertEqual([row['id'] for row in data['experiments']['recent']],list(reversed(expected))[:5])
        self.assertTrue(all(row['defense']=='filter' for row in data['experiments']['recent']))
        self.assertEqual((data['performance']['total'],data['performance']['completed'],data['performance']['modelCount']),(4,4,2))
        self.assertEqual(len(data['performance']['recent']),4)
        self.assertEqual(data['performance']['recent'][0]['models'],['model-b'])
        self.assertNotIn('PRIVATE_',json.dumps(data))
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT * FROM experiments ORDER BY id').fetchall(),before)

    async def test_recent_defense_labels_follow_recorded_runs_instead_of_requested_plan(self):
        from backend.defenses.durable import profile
        fixtures = [
            ('actual-off','both',[(False,{})],'off'),
            ('conflicting-on','off',[(True,{})],'unknown'),
            ('recorded-mode','off',[(True,{'defenseMode':None,'defensePlan':{'defenseResult':{'mode':'separate'}}})],'separate'),
            ('mixed','both',[(False,{}),(True,{'defenseMode':'filter'})],'mixed'),
            ('conflicting-off','filter',[(False,{'defenseMode':'filter'})],'unknown'),
        ]
        with self.db.connect() as conn:
            defense_id = profile(conn,'filter','dashboard-test')
            for name,planned,runs,expected in fixtures:
                eid = str(uuid4())
                insert(conn,'experiments',{'id':eid,'name':name,'purpose':'performance','status':'queued',
                    'evaluation_policy':{},'execution_plan':{'defense':planned},
                    'environment_snapshot':{},'created_by':'dashboard-test'})
                for index,(enabled,snapshot) in enumerate(runs,1):
                    cid,rid = str(uuid4()),str(uuid4())
                    insert(conn,'experiment_cases',{'id':cid,'experiment_id':eid,'case_number':index,
                        'repeat_index':1,'expected_label':'normal','provider':'baze','requested_model':'model-a',
                        'prompt_version':'test','system_prompt':'PRIVATE_SYSTEM','inference_settings':{},
                        'input_snapshot':snapshot,'input_sha256':'a'*64})
                    insert(conn,'runs',{'id':rid,'experiment_case_id':cid,'security_gateway_enabled':enabled,
                        'defense_profile_id':defense_id if enabled else None,'idempotency_key':rid,'status':'queued'})
        data = await self.dashboard()
        expected = {name:result for name,_,_,result in fixtures}
        for section in ('experiments','performance'):
            self.assertEqual({row['name']:row['defense'] for row in data[section]['recent']},expected)
        self.assertNotIn('PRIVATE_SYSTEM',json.dumps(data))


if __name__ == '__main__':
    unittest.main()
