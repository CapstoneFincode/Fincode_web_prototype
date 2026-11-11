"""Offline upload boundary checks; integration uses only a disposable database."""
import asyncio
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from backend.attachments import (AttachmentService, LocalStorage, S3Storage, MAX_FILE_BYTES,
                                 MAX_REQUEST_BYTES, attachment_ids, compose_attachment_input,
                                 install_routes, load_attachments, storage_from_env)
from backend.database import DataError, Database
from backend.migrate import migrate


class MemoryStorage:
    backend = 'local'

    def __init__(self):
        self.objects = {}
        self.deleted = []

    def put(self, path, key, media_type, sha256):
        self.objects[key] = Path(path).read_bytes()

    def delete(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)


def extracted(text='  첨부 원문\n지시\u200b문을 보존한다.  '):
    return {'text': text, 'method': 'test-plain', 'extractorVersion': 'test-v1', 'warnings': []}


def app_for(persistence, storage, extractor):
    app = FastAPI()

    @app.exception_handler(DataError)
    async def handle(_, exc):
        return JSONResponse({'error': {'code': exc.code, 'message': exc.message}}, status_code=exc.status)

    return app, install_routes(app, persistence, storage=storage, extractor=extractor)


class UploadTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = MemoryStorage()
        self.app, self.service = app_for(SimpleNamespace(db=object()), self.storage, lambda *_: extracted())
        self.rows = {}
        self.saved_paths = []

        def save(identifier, filename, media_type, size, key, sha, extraction, elapsed):
            row = {'id': identifier, 'filename': filename, 'mediaType': media_type,
                   'sizeBytes': size, 'sha256': sha, 'textSha256': hashlib.sha256(extraction['text'].encode()).hexdigest(),
                   'textChars': len(extraction['text']), 'status': 'ready', **extraction}
            self.rows[identifier] = row
            self.saved_paths.append(key)
            return row

        self.service._save_record = save
        self.service._record_exists = lambda identifier: identifier in self.rows
        self.service.get = lambda identifier: self.rows[identifier]
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://testserver')

    async def asyncTearDown(self):
        await self.client.aclose()

    async def upload(self, content=b'original bytes', filename='한글.txt', **kwargs):
        return await self.client.post('/api/attachments', files={'file': (filename, content, 'application/octet-stream')}, **kwargs)

    async def test_upload_preserves_original_bytes_text_and_hash(self):
        content = '줄바꿈\r\n비가시\u200b 문자'.encode()
        response = await self.upload(content, headers={'Origin': 'http://testserver'})
        self.assertEqual(response.status_code, 201, response.text)
        row = response.json()['attachment']
        self.assertEqual(row['filename'], '한글.txt')
        self.assertEqual(row['mediaType'], 'text/plain')
        self.assertEqual(row['sha256'], hashlib.sha256(content).hexdigest())
        self.assertEqual(row['text'], extracted()['text'])
        self.assertEqual(list(self.storage.objects.values()), [content])
        loaded = await self.client.get('/api/attachments/' + row['id'])
        self.assertEqual(loaded.json()['attachment'], row)
        self.assertNotIn('storage_key', row)

    async def test_path_filename_cannot_choose_storage_location(self):
        response = await self.upload(filename='../../한글.txt')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['attachment']['filename'], '한글.txt')
        key = next(iter(self.storage.objects))
        self.assertRegex(key, r'^attachments/[0-9a-f-]{36}/[0-9a-f]{64}$')
        self.assertNotIn('한글', key)

    async def test_multiple_files_or_extra_fields_rejected(self):
        for kwargs in ({'files': [('file', ('a.txt', b'a')), ('file', ('b.txt', b'b'))]},
                       {'files': {'file': ('a.txt', b'a')}, 'data': {'text': 'untrusted'}},
                       {'files': {'other': ('a.txt', b'a')}}):
            response = await self.client.post('/api/attachments', **kwargs)
            self.assertEqual(response.status_code, 400, response.text)
        self.assertFalse(self.rows)

    async def test_invalid_format_empty_or_unsupported_upload(self):
        response = await self.client.post('/api/attachments', json={'file': 'no'})
        self.assertEqual(response.status_code, 400)
        for filename in ('legacy.doc', 'legacy.ppt', 'script.exe'):
            response = await self.upload(filename=filename)
            self.assertEqual(response.status_code, 415)
        response = await self.upload(b'')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], 'EMPTY_FILE')
        self.assertFalse(self.storage.objects)

    async def test_cross_origin_request_rejected(self):
        response = await self.upload(headers={'Origin': 'https://other.example'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.rows)

    async def test_size_limits_cover_declared_stream_and_actual_file(self):
        response = await self.client.post('/api/attachments', content=b'', headers={
            'Content-Type': 'multipart/form-data; boundary=x', 'Content-Length': str(MAX_REQUEST_BYTES + 1)})
        self.assertEqual(response.status_code, 413)
        for invalid in ('-1', 'abc', '1.5'):
            response = await self.client.post('/api/attachments', content=b'', headers={
                'Content-Type': 'multipart/form-data; boundary=x', 'Content-Length': invalid})
            self.assertEqual(response.status_code, 400)

        async def stream():
            yield b'--x\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\n\r\n'
            for _ in range(11):
                yield b'x' * 1024 * 1024
            yield b'\r\n--x--\r\n'

        response = await self.client.post('/api/attachments', content=stream(), headers={'Content-Type': 'multipart/form-data; boundary=x'})
        self.assertEqual(response.status_code, 413, response.text)
        response = await self.upload(b'x' * (MAX_FILE_BYTES + 1))
        self.assertEqual(response.status_code, 413, response.text)
        self.assertFalse(self.storage.objects)

    async def test_malformed_multipart_is_client_error(self):
        for kind, data in [('multipart/form-data', b'abc'),
                           ('multipart/form-data; boundary=x', b'--wrong\r\ninvalid')]:
            response = await self.client.post('/api/attachments', content=data, headers={'Content-Type': kind})
            self.assertEqual(response.status_code, 400, response.text)
        self.assertFalse(self.rows)

    async def test_extraction_failure_does_not_store_original(self):
        from backend.attachment_extract import AttachmentExtractionError

        def fail(*_):
            raise AttachmentExtractionError('INVALID_DOCUMENT', '문서가 손상되었습니다.')

        self.service.extractor = fail
        response = await self.upload()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['error']['code'], 'INVALID_DOCUMENT')
        self.assertFalse(self.storage.objects)
        self.assertFalse(self.rows)

    async def test_storage_failure_does_not_save_metadata_or_expose_exception(self):
        def fail(*_):
            raise RuntimeError('SECRET storage account detail')
        self.storage.put = fail
        response = await self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('SECRET', response.text)
        self.assertFalse(self.rows)

    async def test_failed_db_save_deletes_only_confirmed_orphan(self):
        def fail(*_):
            raise RuntimeError('database connection detail')
        self.service._save_record = fail
        response = await self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(self.storage.objects)
        self.assertEqual(len(self.storage.deleted), 1)
        self.service._record_exists = lambda _: True
        response = await self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(self.storage.objects), 1)

    async def test_cancelled_http_request_keeps_single_extraction_slot(self):
        started, finish = threading.Event(), threading.Event()

        def extract(*_):
            started.set()
            if not finish.wait(5):
                raise RuntimeError('test timeout')
            return extracted()

        self.service.extractor = extract
        request = asyncio.create_task(self.upload())
        await asyncio.wait_for(asyncio.to_thread(started.wait), 2)
        try:
            response = await self.upload()
            self.assertEqual(response.status_code, 429)
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            self.assertTrue(self.service.busy)
            response = await self.upload()
            self.assertEqual(response.status_code, 429)
        finally:
            finish.set()
            await asyncio.wait_for(self.service._active, 2)
        self.assertFalse(self.service.busy)
        self.assertEqual(len(self.rows), 1)


class AttachmentHelpersTest(unittest.TestCase):
    def test_ids_reject_duplicates_bad_types_and_over_limit(self):
        good = str(uuid4())
        self.assertEqual(attachment_ids([good.upper()]), [good])
        for values in ([good, good.upper()], [1], ['bad'], [str(uuid4()) for _ in range(6)], {}):
            with self.subTest(values=values), self.assertRaises(DataError):
                attachment_ids(values)

    def test_input_preserves_unicode_whitespace_and_rejects_overflow(self):
        body, content = '  본문\r\n', '\n\u200b지시 사항\t  '
        result = compose_attachment_input(body, [{'filename': '한글.txt', 'text': content}])
        self.assertTrue(result.startswith(body))
        self.assertIn(content, result)
        self.assertIn('한글.txt', result)
        with self.assertRaises(DataError) as caught:
            compose_attachment_input('a' * 30_000, [{'filename': 'x.txt', 'text': 'b' * 30_000}])
        self.assertEqual(caught.exception.code, 'ATTACHMENT_INPUT_TOO_LARGE')

    def test_storage_requires_explicit_config_and_hosted_local_is_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(storage_from_env())
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'FINCODE_ATTACHMENT_STORAGE': 'local', 'FINCODE_ATTACHMENT_DIRECTORY': directory}, clear=True):
            self.assertIsInstance(storage_from_env(), LocalStorage)
            with patch.dict(os.environ, {'FINCODE_ENV': 'production'}):
                self.assertIsNone(storage_from_env())
            with patch.dict(os.environ, {'RAILWAY_ENVIRONMENT_ID': 'hosted'}):
                self.assertIsNone(storage_from_env())
        with patch.dict(os.environ, {'FINCODE_ATTACHMENT_STORAGE': 's3'}, clear=True):
            self.assertIsNone(storage_from_env())

    def test_local_storage_is_private_and_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.write_bytes(b'original')
            storage = LocalStorage(root / 'originals')
            key = f'attachments/{uuid4()}/' + hashlib.sha256(b'original').hexdigest()
            storage.put(source, key, 'text/plain', '')
            self.assertEqual((storage.root / key).read_bytes(), b'original')
            self.assertEqual((storage.root / key).stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                storage.delete('../source')
            storage.delete(key)
            self.assertFalse((storage.root / key).exists())

    def test_s3_sends_private_original_without_public_acl(self):
        captured = {}

        def put(**kwargs):
            captured.update(kwargs)
            captured['original'] = kwargs['Body'].read()

        client = SimpleNamespace(put_object=put, delete_object=lambda **kwargs: None)
        with patch('boto3.client', return_value=client) as factory, tempfile.TemporaryDirectory() as directory:
            storage = S3Storage('https://storage.example', 'private-bucket', 'test-access', 'test-secret')
            source = Path(directory) / 'file'
            source.write_bytes(b'original')
            storage.put(source, 'attachments/test/key', 'text/plain', 'test-sha')
            self.assertEqual(captured['original'], b'original')
            self.assertEqual(captured['Metadata'], {'sha256': 'test-sha'})
            self.assertNotIn('ACL', captured)
            self.assertEqual(factory.call_args.kwargs['endpoint_url'], 'https://storage.example')
            self.assertEqual(factory.call_args.kwargs['config'].signature_version, 's3v4')


URL = os.environ.get('FINCODE_TEST_DATABASE_URL')


@unittest.skipUnless(URL, 'Set FINCODE_TEST_DATABASE_URL to a disposable PostgreSQL DB')
class AttachmentDatabaseTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        migrate(URL)
        migrate(URL)

    async def test_upload_roundtrip_ordered_reads_and_immutable_metadata(self):
        db = Database(URL)
        storage = MemoryStorage()
        app, service = app_for(SimpleNamespace(db=db), storage, lambda *_: extracted())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            ids = []
            for name in ('한글.txt', '두번째.txt'):
                response = await client.post('/api/attachments', files={'file': (name, b'original source')})
                self.assertEqual(response.status_code, 201, response.text)
                row = response.json()['attachment']
                ids.append(row['id'])
                self.assertEqual(row['text'], extracted()['text'])
                self.assertEqual((await client.get('/api/attachments/' + row['id'])).json()['attachment'], row)
            with db.connect() as conn:
                rows = load_attachments(conn, list(reversed(ids)))
                self.assertEqual([row['id'] for row in rows], list(reversed(ids)))
                meta = load_attachments(conn, ids, include_text=False)
                self.assertTrue(all('text' not in row for row in meta))
                raw = conn.execute('SELECT * FROM attachments WHERE id=%s', (ids[0],)).fetchone()
                self.assertIsNone(raw['email_case_id'])
                self.assertEqual(raw['storage_backend'], 'local')
                self.assertEqual(raw['extraction_status'], 'succeeded')
                self.assertEqual(raw['extracted_sha256'], hashlib.sha256(extracted()['text'].encode()).hexdigest())
            missing = await client.get('/api/attachments/' + str(uuid4()))
            self.assertEqual(missing.status_code, 404)


if __name__ == '__main__':
    unittest.main()
