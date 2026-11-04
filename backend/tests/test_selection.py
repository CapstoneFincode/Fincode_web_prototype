"""Oldest-first datasets and bounded selection execution on a disposable DB only."""
from datetime import datetime, timezone
import os
import unittest
from uuid import uuid4

import httpx

from backend.app import CONFIG, create_app
from backend.database import Database
from backend.migrate import migrate


URL = os.environ.get('FINCODE_TEST_DATABASE_URL')
ATTACK = {'name':'선택 테스트', 'vector':'direct', 'channel':'input',
          'technique':'override', 'goal':'marker', 'payload':'SELECTION_TEST',
          'criterion':'응답 검토', 'categoryId':'instruction-override'}


@unittest.skipUnless(URL, 'Set FINCODE_TEST_DATABASE_URL to a disposable PostgreSQL DB')
class SelectionDatabaseTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        migrate(URL)
        cls.db = Database(URL)
        cls.db.seed()

    async def asyncSetUp(self):
        with self.db.connect() as conn:
            conn.execute('TRUNCATE experiments CASCADE')
        def forbidden(request):
            raise AssertionError('Selection checks must not call a model')
        app = create_app(api_key='offline-only', database_url=URL, run_worker=False,
                         upstream_transport=httpx.MockTransport(forbidden))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver')

    async def asyncTearDown(self):
        await self.client.aclose()

    def make_attacks(self, count):
        keys = []
        with self.db.connect() as conn:
            for number in range(count):
                key = 'ATK-SELECT-' + str(uuid4())
                keys.append(key)
                self.db._save(conn, 'attacks', {**ATTACK, 'payload':f'RUN_{number}'}, key)
        rows = {a['id']:a for a in self.db.datasets()['attacks']}
        return [rows[key] for key in keys]

    def payload(self, attacks, **changes):
        return {'name':'등록순 선택', 'kind':'attack', 'model':'offline-test',
                'emailId':self.db.datasets()['normal'][0]['dbId'],
                'scenarioIds':[a['dbId'] for a in attacks], 'repeat':1,
                'defense':'off', 'idempotencyKey':str(uuid4()), **changes}

    async def test_original_registration_time_and_order_survive_edit_and_archive(self):
        first, second = self.make_attacks(2)
        with self.db.connect() as conn:
            conn.execute('UPDATE attack_scenarios SET created_at=%s WHERE id=%s',
                         (datetime(2026,1,1,tzinfo=timezone.utc), first['dbId']))
            conn.execute('UPDATE attack_scenarios SET created_at=%s WHERE id=%s',
                         (datetime(2026,1,2,tzinfo=timezone.utc), second['dbId']))
        before = (await self.client.get('/api/data')).json()['attacks']
        first = next(a for a in before if a['id'] == first['id'])
        self.assertEqual(first['registeredAt'], '2026-01-01T00:00:00Z')
        edited = await self.client.put('/api/data/attacks/' + first['id'], json={**first,'name':'수정된 첫 번째 공격'})
        self.assertEqual(edited.status_code, 200, edited.text)
        after = edited.json()['attacks']
        current = next(a for a in after if a['id'] == first['id'])
        self.assertEqual(current['dbVersion'], 2)
        self.assertNotEqual(current['dbId'], first['dbId'])
        self.assertEqual(current['registeredAt'], first['registeredAt'])
        selected = [a['id'] for a in after if a['id'] in {first['id'], second['id']}]
        self.assertEqual(selected, [first['id'], second['id']])
        removed = await self.client.request('DELETE', '/api/data/attacks/' + first['id'], json={'dbVersion':2})
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertFalse(any(a['id'] == first['id'] for a in removed.json()['attacks']))
        with self.db.connect() as conn:
            count = conn.execute('SELECT count(*) AS n FROM attack_scenarios WHERE scenario_key=%s', (first['id'],)).fetchone()['n']
        self.assertEqual(count, 2)

    async def test_one_hundred_distinct_attacks_once_and_idempotent_replay(self):
        attacks = self.make_attacks(100)
        payload = self.payload(attacks)
        response = await self.client.post('/api/experiments', json=payload)
        self.assertEqual(response.status_code, 202, response.text)
        replay = await self.client.post('/api/experiments', json=payload)
        self.assertEqual(replay.status_code, 202, replay.text)
        self.assertEqual(replay.json()['id'], response.json()['id'])
        self.assertEqual(replay.json()['runId'], response.json()['runId'])
        detail = (await self.client.get('/api/experiments/' + response.json()['id'])).json()
        self.assertEqual(len(detail['runs']), 100)
        self.assertTrue(all(r['repeat_index'] == 1 for r in detail['runs']))
        self.assertTrue(all(r['status'] == 'queued' for r in detail['runs']))
        for changes in [
            {'repeat':2},
            {'scenarioIds':payload['scenarioIds'] + [str(uuid4())]},
            {'scenarioIds':[attacks[0]['dbId'], attacks[0]['dbId']]},
        ]:
            invalid = await self.client.post('/api/experiments', json={**payload, 'idempotencyKey':str(uuid4()), **changes})
            self.assertEqual(invalid.status_code, 400, invalid.text)
        conflict = await self.client.post('/api/experiments', json={**payload, 'scenarioIds':payload['scenarioIds'][:-1]})
        self.assertEqual(conflict.status_code, 409, conflict.text)
        experiments = (await self.client.get('/api/experiments')).json()['experiments']
        self.assertEqual(len(experiments), 1)
        legacy = await self.client.post('/api/experiments', json=self.payload(attacks[:10], repeat=10))
        self.assertEqual(legacy.status_code, 202, legacy.text)
        self.assertEqual(len((await self.client.get('/api/experiments/' + legacy.json()['id'])).json()['runs']), 100)

    async def test_queue_claims_cases_in_submitted_order_then_repeat_order(self):
        attacks = self.make_attacks(3)
        chosen = [attacks[2], attacks[0], attacks[1]]
        response = await self.client.post('/api/experiments', json=self.payload(chosen, repeat=2))
        self.assertEqual(response.status_code, 202, response.text)
        seen = []
        for _ in range(6):
            run = self.db.claim_next(CONFIG)
            self.assertIsNotNone(run)
            seen.append(run['messages'][-1]['content'])
            self.assertTrue(self.db.finish(run['runId'], {'summary':'mock only'}, 'succeeded', 0))
        self.assertEqual(seen, ['RUN_2','RUN_2','RUN_0','RUN_0','RUN_1','RUN_1'])
        self.assertIsNone(self.db.claim_next(CONFIG))
