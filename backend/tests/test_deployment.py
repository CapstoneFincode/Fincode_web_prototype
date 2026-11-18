"""Hosted startup and readiness checks without credentials or model requests."""
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from backend.app import create_app
from backend.deployment import server_port, validate_hosted


class DeploymentTest(unittest.IsolatedAsyncioTestCase):
    def test_hosted_configuration_fails_before_startup(self):
        for marker in [{'FINCODE_ENV': 'production'}, {'RAILWAY_ENVIRONMENT_ID': 'test-environment'}]:
            with patch.dict(os.environ, marker, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'DATABASE_URL'):
                    validate_hosted('')
                with self.assertRaisesRegex(RuntimeError, 'BACKEND_SHARED_TOKEN'):
                    validate_hosted('postgresql://test')
                for token in ['short', 'x' * 31 + '\n', '비밀' * 32]:
                    os.environ['BACKEND_SHARED_TOKEN'] = token
                    with self.assertRaises(RuntimeError):
                        validate_hosted('postgresql://test')
                os.environ['BACKEND_SHARED_TOKEN'] = 'x' * 48
                validate_hosted('postgresql://test')

    def test_provider_port_is_validated(self):
        for value in ['0', '65536', 'bad', '8000;echo x']:
            with patch.dict(os.environ, {'PORT': value}):
                with self.assertRaises(RuntimeError):
                    server_port()
        with patch.dict(os.environ, {'PORT': '9123'}):
            self.assertEqual(server_port(), 9123)

    async def test_asgi_startup_rejects_missing_auth(self):
        with patch.dict(os.environ, {'FINCODE_ENV':'production'}, clear=True):
            app = create_app(database_url='postgresql://test', run_worker=False)
            with self.assertRaisesRegex(RuntimeError, 'BACKEND_SHARED_TOKEN'):
                async with app.router.lifespan_context(app):
                    self.fail('Unprotected hosted startup was allowed')
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                self.assertEqual((await client.get('/api/data')).status_code,503)

    async def test_readiness_requires_database_and_does_not_expose_details(self):
        for database, expected in [({'configured':False,'ready':False},503),({'configured':True,'ready':False},503),({'configured':True,'ready':True},200)]:
            with patch.dict(os.environ, {'BACKEND_SHARED_TOKEN':'x'*48}, clear=True), patch('backend.persistence.Persistence.health',new=AsyncMock(return_value=database)):
                app = create_app(database_url='', run_worker=False)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                    response = await client.get('/api/ready')
                    self.assertEqual(response.status_code,expected)
                    self.assertEqual(response.json(),{'ready':expected == 200})
                    self.assertEqual(response.headers['cache-control'],'no-store')
                    self.assertEqual((await client.get('/api/data')).status_code,401)

    def test_launcher_never_starts_server_after_failed_migration(self):
        from backend.start import main
        with patch.dict(os.environ, {'DATABASE_URL':'postgresql://test','PORT':'8123'}, clear=True), patch('backend.start.migrate',side_effect=Exception('sensitive DSN')), patch('backend.start.os.execv') as execute, patch('sys.stderr') as output:
            self.assertEqual(main(),1)
            execute.assert_not_called()
            self.assertNotIn('sensitive', ''.join(str(x) for x in output.write.call_args_list))
