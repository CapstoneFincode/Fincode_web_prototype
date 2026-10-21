"""Category API and additive migration checks; only a disposable test DB is used."""
import os
from pathlib import Path
import unittest
from uuid import uuid4

import httpx
from psycopg import sql

from backend.app import create_app
from backend.database import Database, DataError, category_name
from backend.migrate import migrate


URL = os.environ.get('FINCODE_TEST_DATABASE_URL')
ROOT = Path(__file__).resolve().parents[2]
ATTACK = {'name':'카테고리 테스트', 'vector':'direct', 'channel':'input',
          'technique':'override', 'goal':'test marker', 'payload':'Return CATEGORY_TEST',
          'criterion':'정확히 CATEGORY_TEST 출력', 'expectedOutput':'CATEGORY_TEST'}


class CategoryNameTest(unittest.TestCase):
    def test_whitespace_unicode_and_case_share_a_canonical_key(self):
        self.assertEqual(category_name('  ＡＰＩ\t  Test  '), ('API Test', 'api test'))
        self.assertEqual(category_name('API TEST')[1], category_name('api test')[1])
        for value in ['', ' \n\t', None, 3, 'x' * 61, 'hidden\u200bname']:
            with self.subTest(value=value), self.assertRaises(DataError):
                category_name(value)


@unittest.skipUnless(URL, 'Set FINCODE_TEST_DATABASE_URL to a disposable PostgreSQL DB')
class CategoryDatabaseTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        migrate(URL)
        migrate(URL)
        cls.db = Database(URL)
        cls.db.seed()

    async def asyncSetUp(self):
        def forbidden(request):
            raise AssertionError('Category checks must not call a model')
        self.app = create_app(api_key='offline-only', database_url=URL, run_worker=False,
                              upstream_transport=httpx.MockTransport(forbidden))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://testserver')

    async def asyncTearDown(self):
        await self.client.aclose()

    async def create_category(self):
        name = 'Team ' + str(uuid4())
        response = await self.client.post('/api/categories', json={'name':'  ' + name + '  '})
        self.assertEqual(response.status_code, 201, response.text)
        return next(c for c in response.json()['categories'] if c['name'] == name)

    async def test_category_creation_round_trip_and_duplicate_rejection(self):
        category = await self.create_category()
        fresh = await self.client.get('/api/data')
        self.assertIn(category, fresh.json()['categories'])
        duplicate = await self.client.post('/api/categories', json={'name':category['name'].upper()})
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        self.assertEqual(duplicate.json()['error']['code'], 'CATEGORY_EXISTS')
        for name in ['', '   ', 'x'*61, None]:
            invalid = await self.client.post('/api/categories', json={'name':name})
            self.assertEqual(invalid.status_code, 400, invalid.text)
        denied = await self.client.post('/api/categories', json={'name':'untrusted'}, headers={'Origin':'https://evil.example'})
        self.assertEqual(denied.status_code, 403)

    async def test_categories_persist_through_revisions_and_legacy_edits(self):
        category = await self.create_category()
        created = await self.client.post('/api/data/attacks', json={**ATTACK, 'categoryId':category['id']})
        self.assertEqual(created.status_code, 200, created.text)
        attack = created.json()['attacks'][-1]
        self.assertEqual(attack['categoryId'], category['id'])
        legacy = {k:v for k,v in attack.items() if k != 'categoryId'}
        legacy['name'] = '구형 클라이언트 수정'
        updated = await self.client.put('/api/data/attacks/' + attack['id'], json=legacy)
        self.assertEqual(updated.status_code, 200, updated.text)
        current = next(a for a in updated.json()['attacks'] if a['id'] == attack['id'])
        self.assertEqual(current['categoryId'], category['id'])
        self.assertEqual(current['dbVersion'], 2)
        changed = await self.client.put('/api/data/attacks/' + attack['id'], json={**current, 'categoryId':'special-token'})
        self.assertEqual(changed.status_code, 200, changed.text)
        with self.db.connect() as conn:
            previous = conn.execute('SELECT category_id,payload FROM attack_scenarios WHERE id=%s', (attack['dbId'],)).fetchone()
        self.assertEqual(previous['category_id'], category['id'])
        self.assertEqual(previous['payload'], ATTACK['payload'])

    async def test_unknown_categories_rejected_and_old_payload_defaults(self):
        before = (await self.client.get('/api/data')).json()
        for category in ['missing-category', '', None]:
            response = await self.client.post('/api/data/attacks', json={**ATTACK, 'categoryId':category})
            self.assertEqual(response.status_code, 400, response.text)
        after = (await self.client.get('/api/data')).json()
        self.assertEqual(len(before['attacks']), len(after['attacks']))
        created = await self.client.post('/api/data/attacks', json=ATTACK)
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()['attacks'][-1]['categoryId'], 'uncategorized')
        sample = next(a for a in created.json()['attacks'] if a['id'] == 'ATK-S07')
        self.assertEqual(sample['categoryId'], 'special-token')

    async def test_queued_experiment_preserves_category_after_scenario_edit(self):
        category = await self.create_category()
        created = await self.client.post('/api/data/attacks', json={**ATTACK, 'categoryId':category['id']})
        data = created.json()
        attack, email = data['attacks'][-1], data['normal'][0]
        queued = await self.client.post('/api/experiments', json={
            'name':'카테고리 스냅샷', 'kind':'attack', 'model':'offline-test',
            'emailId':email['dbId'], 'scenarioIds':[attack['dbId']], 'repeat':1,
            'defense':'off', 'idempotencyKey':str(uuid4())})
        self.assertEqual(queued.status_code, 202, queued.text)
        changed = await self.client.put('/api/data/attacks/' + attack['id'], json={**attack, 'categoryId':'special-token'})
        self.assertEqual(changed.status_code, 200, changed.text)
        detail = await self.client.get('/api/experiments/' + queued.json()['id'])
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()['runs'][0]['input_snapshot']['attackCategoryId'], category['id'])

    async def test_additive_migration_preserves_old_user_rows_and_ids(self):
        # Isolated fixture schema inside the explicitly disposable test DB.
        schema = 'category_upgrade_' + uuid4().hex
        source = (ROOT / 'database/migrations/003_attack_categories.sql').read_text()
        user_id, sample_id = str(uuid4()), str(uuid4())
        with self.db.connect() as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            conn.execute(sql.SQL('CREATE TABLE {}.attack_scenarios (id uuid PRIMARY KEY, scenario_key text, provenance text, payload text)').format(sql.Identifier(schema)))
            conn.execute(sql.SQL('INSERT INTO {}.attack_scenarios VALUES (%s,%s,%s,%s),(%s,%s,%s,%s)').format(sql.Identifier(schema)),
                         (user_id, 'ATK-S07', 'team', 'original user text', sample_id, 'ATK-S07', 'sample', 'original sample'))
            conn.execute(source.replace('fincode.', schema + '.'), prepare=False)
            rows = conn.execute(sql.SQL('SELECT id,category_id,payload FROM {}.attack_scenarios').format(sql.Identifier(schema))).fetchall()
            mapped = {str(r['id']):r for r in rows}
            self.assertEqual(mapped[user_id]['category_id'], 'uncategorized')
            self.assertEqual(mapped[user_id]['payload'], 'original user text')
            self.assertEqual(mapped[sample_id]['category_id'], 'special-token')
            self.assertEqual(mapped[sample_id]['payload'], 'original sample')
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
