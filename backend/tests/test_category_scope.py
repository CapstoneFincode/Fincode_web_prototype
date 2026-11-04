"""Scope metadata compatibility and history preservation in a disposable DB only."""
from contextlib import contextmanager
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from backend.app import create_app
from backend.database import Database, DataError, category_scope
from backend.migrate import migrate


URL = os.environ.get('FINCODE_TEST_DATABASE_URL')
ROOT = Path(__file__).resolve().parents[2]
ATTACK = {'name':'범위와 별도인 간접 공격', 'vector':'indirect', 'channel':'email',
          'technique':'override', 'goal':'scope test', 'payload':'Return SCOPE_TEST',
          'criterion':'응답 검토', 'expectedOutput':'SCOPE_TEST'}


class CategoryScopeValidationTest(unittest.TestCase):
    def test_scope_requires_one_known_string(self):
        for value in ('direct', 'indirect', 'both'):
            self.assertEqual(category_scope(value), value)
        for value in ('', 'all', 'DIRECT', ' both ', None, False, 0, [], {}):
            with self.subTest(value=value), self.assertRaises(DataError):
                category_scope(value)


@unittest.skipUnless(URL, 'Set FINCODE_TEST_DATABASE_URL to a disposable PostgreSQL DB')
class CategoryScopeDatabaseTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        migrate(URL)
        migrate(URL)
        cls.db = Database(URL)
        cls.db.seed()

    async def asyncSetUp(self):
        def forbidden(request):
            raise AssertionError('Scope checks must not call a model')
        app = create_app(api_key='offline-only', database_url=URL, run_worker=False,
                         upstream_transport=httpx.MockTransport(forbidden))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver')

    async def asyncTearDown(self):
        await self.client.aclose()

    async def create_category(self, scope=None):
        name = 'Scope ' + str(uuid4())
        body = {'name':name}
        if scope is not None:
            body['scope'] = scope
        response = await self.client.post('/api/categories', json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return next(c for c in response.json()['categories'] if c['name'] == name)

    async def test_creation_defaults_for_old_clients_and_validates_scope(self):
        self.assertEqual((await self.create_category())['scope'], 'both')
        for scope in ('direct', 'indirect', 'both'):
            category = await self.create_category(scope)
            self.assertEqual(category['scope'], scope)
            data = (await self.client.get('/api/data')).json()
            self.assertIn(category, data['categories'])
        for scope in ('', 'all', None, False, ['direct']):
            response = await self.client.post('/api/categories', json={'name':'Invalid ' + str(uuid4()), 'scope':scope})
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(response.json()['error']['code'], 'INVALID_CATEGORY_SCOPE')

    async def test_scope_update_checks_expected_value_and_protects_uncategorized(self):
        category = await self.create_category('direct')
        path = '/api/categories/' + category['id']
        response = await self.client.put(path, json={'scope':'indirect', 'expectedScope':'direct'})
        self.assertEqual(response.status_code, 200, response.text)
        updated = next(c for c in response.json()['categories'] if c['id'] == category['id'])
        self.assertEqual(updated, {**category, 'scope':'indirect'})
        stale = await self.client.put(path, json={'scope':'both', 'expectedScope':'direct'})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()['error']['code'], 'CATEGORY_SCOPE_CONFLICT')
        for body in ({'scope':'both'}, {'scope':'all','expectedScope':'indirect'},
                     {'scope':'both','expectedScope':'indirect','name':'Not a rename'}):
            rejected = await self.client.put(path, json=body)
            self.assertEqual(rejected.status_code, 400, rejected.text)
        missing = await self.client.put('/api/categories/missing', json={'scope':'both','expectedScope':'both'})
        self.assertEqual(missing.status_code, 404)
        protected = await self.client.put('/api/categories/uncategorized', json={'scope':'direct','expectedScope':'both'})
        self.assertEqual(protected.status_code, 400)
        self.assertEqual(protected.json()['error']['code'], 'CATEGORY_SCOPE_FIXED')
        unchanged = (await self.client.get('/api/data')).json()['categories']
        self.assertEqual(next(c for c in unchanged if c['id'] == category['id']), updated)
        self.assertEqual(next(c for c in unchanged if c['id'] == 'uncategorized')['scope'], 'both')
        denied = await self.client.put(path, json={'scope':'both','expectedScope':'indirect'}, headers={'Origin':'https://evil.example'})
        self.assertEqual(denied.status_code, 403)

    async def test_scope_changes_preserve_conflicting_scenarios_and_queued_inputs(self):
        category = await self.create_category('direct')
        created = await self.client.post('/api/data/attacks', json={**ATTACK, 'categoryId':category['id']})
        self.assertEqual(created.status_code, 200, created.text)
        data = created.json()
        attack, email = data['attacks'][-1], data['normal'][0]
        queued = await self.client.post('/api/experiments', json={
            'name':'Scope snapshot', 'kind':'attack', 'model':'offline-test',
            'emailId':email['dbId'], 'scenarioIds':[attack['dbId']], 'repeat':1,
            'defense':'off', 'idempotencyKey':str(uuid4())})
        self.assertEqual(queued.status_code, 202, queued.text)
        detail_path = '/api/experiments/' + queued.json()['id']
        before = (await self.client.get(detail_path)).json()
        changed = await self.client.put('/api/categories/' + category['id'], json={'scope':'indirect','expectedScope':'direct'})
        self.assertEqual(changed.status_code, 200, changed.text)
        current = next(a for a in changed.json()['attacks'] if a['id'] == attack['id'])
        self.assertEqual(current, attack)
        after = (await self.client.get(detail_path)).json()
        self.assertEqual(after['runs'][0]['input_snapshot'], before['runs'][0]['input_snapshot'])
        self.assertEqual(after['runs'][0]['input_snapshot']['attackCategoryId'], category['id'])

    async def test_additive_upgrade_preserves_categories_scenarios_and_snapshots(self):
        schema = 'scope_upgrade_' + uuid4().hex
        source = (ROOT / 'database/migrations/004_category_scope.sql').read_text()
        with self.db.connect() as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            conn.execute(sql.SQL('CREATE TABLE {}.attack_categories (id text PRIMARY KEY, name text, created_by text)').format(sql.Identifier(schema)))
            conn.execute(sql.SQL('INSERT INTO {}.attack_categories VALUES (%s,%s,%s),(%s,%s,%s),(%s,%s,%s)').format(sql.Identifier(schema)),
                         ('attachment-injection','Attachment','seed','custom-empty','Team empty','team','uncategorized','Unclassified','seed'))
            conn.execute(sql.SQL('CREATE TABLE {}.attack_scenarios (id uuid, category_id text, vector text, revision integer, payload text)').format(sql.Identifier(schema)))
            conn.execute(sql.SQL('INSERT INTO {}.attack_scenarios VALUES (%s,%s,%s,%s,%s)').format(sql.Identifier(schema)),
                         (str(uuid4()),'attachment-injection','direct',2,'original payload'))
            conn.execute(sql.SQL('CREATE TABLE {}.runs (id uuid, input_snapshot jsonb)').format(sql.Identifier(schema)))
            conn.execute(sql.SQL('INSERT INTO {}.runs VALUES (%s,%s)').format(sql.Identifier(schema)),
                         (str(uuid4()),Jsonb({'attackCategoryId':'attachment-injection','payload':'original payload'})))
            scenarios = conn.execute(sql.SQL('SELECT * FROM {}.attack_scenarios').format(sql.Identifier(schema))).fetchall()
            snapshots = conn.execute(sql.SQL('SELECT * FROM {}.runs').format(sql.Identifier(schema))).fetchall()
            categories = conn.execute(sql.SQL('SELECT * FROM {}.attack_categories ORDER BY id').format(sql.Identifier(schema))).fetchall()
            conn.execute(source.replace('fincode.',schema + '.'), prepare=False)
            actual = conn.execute(sql.SQL('SELECT * FROM {}.attack_categories ORDER BY id').format(sql.Identifier(schema))).fetchall()
            self.assertEqual([{k:v for k,v in c.items() if k != 'scope'} for c in actual], categories)
            self.assertEqual({c['id']:c['scope'] for c in actual}, {'attachment-injection':'indirect','custom-empty':'both','uncategorized':'both'})
            self.assertEqual(conn.execute(sql.SQL('SELECT * FROM {}.attack_scenarios').format(sql.Identifier(schema))).fetchall(), scenarios)
            self.assertEqual(conn.execute(sql.SQL('SELECT * FROM {}.runs').format(sql.Identifier(schema))).fetchall(), snapshots)
        try:
            for identifier, scope in [('custom-empty','invalid'),('uncategorized','direct')]:
                # Each expected error owns its transaction, including its rollback.
                with self.assertRaises(psycopg.errors.CheckViolation), self.db.connect() as conn:
                    conn.execute(sql.SQL('UPDATE {}.attack_categories SET scope=%s WHERE id=%s').format(sql.Identifier(schema)), (scope,identifier))
        finally:
            with self.db.connect() as conn:
                conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))

    async def test_health_requires_the_scope_column(self):
        schema = 'scope_health_' + uuid4().hex
        with self.db.connect() as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            conn.execute(sql.SQL('CREATE TABLE {}.runs (response_payload jsonb, lease_expires_at timestamptz)').format(sql.Identifier(schema)))
            conn.execute(sql.SQL('CREATE TABLE {}.attack_scenarios (category_id text)').format(sql.Identifier(schema)))
            conn.execute(sql.SQL('CREATE TABLE {}.attack_categories (id text)').format(sql.Identifier(schema)))
        connect = self.db.connect
        @contextmanager
        def fixture_connection():
            with connect() as conn:
                conn.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
                yield conn
        try:
            with patch.object(self.db, 'connect', fixture_connection):
                with self.assertRaises(psycopg.errors.UndefinedColumn):
                    self.db.health()
                with connect() as conn:
                    conn.execute(sql.SQL('ALTER TABLE {}.attack_categories ADD COLUMN scope text').format(sql.Identifier(schema)))
                self.assertTrue(self.db.health()['ready'])
        finally:
            with connect() as conn:
                conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
