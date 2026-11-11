"""Read-only original previews: byte identity, private routes and storage failures."""
from contextlib import contextmanager
import hashlib
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from backend.attachments import LocalStorage, S3Storage, MAX_FILE_BYTES
from backend.tests.test_attachments import app_for


IDENTIFIER = '5f0d0ef8-3bf1-4265-a69b-f199c710e19f'
PNG = b'\x89PNG\r\n\x1a\n' + bytes(range(256))


class FakeDB:
    def __init__(self, row):
        self.row = row

    @contextmanager
    def connect(self):
        yield self

    def execute(self, sql, params):
        assert sql.startswith('SELECT ')
        assert params == (IDENTIFIER,)
        return self

    def fetchone(self):
        return self.row


class ImageRouteTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = LocalStorage(self.temp.name)
        self.row = {'id': IDENTIFIER, 'media_type': 'image/png', 'original_name': '회의 이미지.png',
                    'storage_backend': 'local', 'size_bytes': len(PNG),
                    'file_sha256': hashlib.sha256(PNG).hexdigest()}
        self.row['storage_key'] = f'attachments/{IDENTIFIER}/{self.row["file_sha256"]}'
        source = Path(self.temp.name) / 'source.png'
        source.write_bytes(PNG)
        self.storage.put(source, self.row['storage_key'], 'image/png', self.row['file_sha256'])
        self.db = FakeDB(self.row)
        self.app, self.service = app_for(SimpleNamespace(db=self.db), self.storage, None)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temp.cleanup()

    async def get(self, identifier=IDENTIFIER):
        return await self.client.get(f'/api/attachments/{identifier}/image')

    async def test_exact_original_bytes_and_private_image_headers(self):
        response = await self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PNG)
        self.assertEqual(response.headers['content-type'], 'image/png')
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
        self.assertIn("inline; filename*=UTF-8''", response.headers['content-disposition'])
        self.assertNotIn('storage_key', response.headers)

    async def test_bad_id_missing_metadata_and_missing_original(self):
        self.assertEqual((await self.get('bad-id')).status_code, 400)
        self.db.row = None
        self.assertEqual((await self.get()).status_code, 404)
        self.db.row = self.row
        self.storage.delete(self.row['storage_key'])
        response = await self.get()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error']['code'], 'ATTACHMENT_ORIGINAL_NOT_FOUND')

    async def test_non_images_and_storage_mismatch_are_not_served(self):
        self.row['media_type'] = 'text/html'
        self.assertEqual((await self.get()).status_code, 415)
        self.row['media_type'] = 'image/png'
        self.row['storage_backend'] = 's3'
        self.assertEqual((await self.get()).status_code, 503)

    async def test_original_tampering_is_detected_and_errors_do_not_expose_secrets(self):
        self.row['file_sha256'] = '0' * 64
        response = await self.get()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'ATTACHMENT_INTEGRITY_ERROR')
        with patch.object(self.storage, 'read', side_effect=RuntimeError('private-storage-secret')):
            response = await self.get()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private-storage-secret', response.text)

    async def test_existing_backend_auth_also_protects_original_route(self):
        from backend.app import create_app
        with patch.dict(os.environ, {'BACKEND_SHARED_TOKEN': 'offline-test-token'}, clear=True):
            app = create_app(api_key='', database_url='', run_worker=False, attachment_storage=self.storage)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.get(f'/api/attachments/{IDENTIFIER}/image')
            self.assertEqual(response.status_code, 401)


class ImageStorageTest(unittest.TestCase):
    def test_s3_read_is_bounded_and_closes_body(self):
        body = io.BytesIO(b'x' * (MAX_FILE_BYTES + 2))
        storage = object.__new__(S3Storage)
        storage.bucket = 'test-bucket'
        storage.client = SimpleNamespace(get_object=lambda **_: {'Body': body})
        data = storage.read(f'attachments/{IDENTIFIER}/' + 'a' * 64)
        self.assertEqual(len(data), MAX_FILE_BYTES + 1)
        self.assertTrue(body.closed)
        with self.assertRaises(ValueError):
            storage.read('../secret')


if __name__ == '__main__':
    unittest.main()
