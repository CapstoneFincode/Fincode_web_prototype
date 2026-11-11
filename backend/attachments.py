"""Bounded attachment upload, private originals and immutable extracted text.

Only server-resolved attachment IDs enter model inputs. No parser output is
interpreted as executable code, a system message, or a client-provided override.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import unicodedata
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import Request
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from python_multipart.exceptions import MultipartParseError

from backend.deployment import is_hosted


MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_FILE_BYTES + 64 * 1024
MAX_FILES = 5
MAX_EXTRACTED_CHARS = 50_000
MAX_INPUT_CHARS = 60_000
MEDIA_TYPES = {
    '.txt': 'text/plain', '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.hwp': 'application/x-hwp', '.hwpx': 'application/hwp+zip',
}
_OBJECT_KEY = re.compile(r'attachments/[0-9a-f-]{36}/[0-9a-f]{64}\Z')


def error(message, status=400, code='INVALID_ATTACHMENT'):
    # Database can import the pure input helpers without an import cycle.
    from backend.database import DataError
    return DataError(message, status, code)


def attachment_ids(value):
    if not isinstance(value, list) or len(value) > MAX_FILES:
        raise error('첨부파일은 최대 5개까지 선택해 주세요.')
    result = []
    for item in value:
        if not isinstance(item, str):
            raise error('첨부파일 ID 형식을 확인해 주세요.')
        try:
            identifier = str(UUID(item))
        except (ValueError, TypeError, AttributeError):
            raise error('첨부파일 ID 형식을 확인해 주세요.') from None
        if identifier in result:
            raise error('같은 첨부파일을 중복 선택할 수 없습니다.')
        result.append(identifier)
    return result


def _public(row, include_text=True):
    metadata = row.get('extraction_metadata') or {}
    result = {
        'id': str(row['id']), 'filename': row['original_name'],
        'mediaType': row['media_type'], 'sizeBytes': row['size_bytes'],
        'sha256': row['file_sha256'], 'textSha256': row['extracted_sha256'],
        'textChars': len(row['extracted_text'] or ''), 'status': 'ready',
        'method': row['extractor_name'], 'extractorVersion': row['extractor_version'],
        'warnings': metadata.get('warnings', []),
    }
    if metadata.get('pageCount') is not None:
        result['pageCount'] = metadata['pageCount']
    if include_text:
        result['text'] = row['extracted_text']
    return result


def load_attachments(conn, ids, include_text=True):
    """Read immutable successful extraction rows in the caller's chosen order."""
    ids = attachment_ids(ids)
    if not ids:
        return []
    rows = conn.execute('SELECT * FROM attachments WHERE id=ANY(%s::uuid[])', (ids,)).fetchall()
    indexed = {str(row['id']): row for row in rows}
    result = []
    for identifier in ids:
        row = indexed.get(identifier)
        if row is None:
            raise error('첨부파일을 찾을 수 없습니다. 파일을 다시 업로드해 주세요.', 404, 'ATTACHMENT_NOT_FOUND')
        if row['extraction_status'] != 'succeeded' or not row.get('extracted_text'):
            raise error('텍스트 추출이 완료된 첨부파일만 사용할 수 있습니다.', 409, 'ATTACHMENT_NOT_READY')
        result.append(_public(row, include_text))
    return result


def compose_attachment_input(body, attachments):
    """Add source labels only; preserve the actual body and extracted text."""
    if not isinstance(body, str):
        raise error('이메일 본문 형식을 확인해 주세요.')
    if not isinstance(attachments, list) or len(attachments) > MAX_FILES:
        raise error('첨부파일은 최대 5개까지 선택해 주세요.')
    parts = [body]
    for index, item in enumerate(attachments, 1):
        if not isinstance(item, dict) or not isinstance(item.get('text'), str):
            raise error('추출된 첨부파일 텍스트가 필요합니다.')
        if len(item['text']) > MAX_EXTRACTED_CHARS:
            raise error('첨부파일의 추출 텍스트는 파일당 50,000자 이하여야 합니다.', 413, 'ATTACHMENT_TEXT_TOO_LARGE')
        name = json.dumps(item.get('filename', '첨부파일'), ensure_ascii=False)
        parts.append(f'\n\n[첨부파일 {index}: {name}]\n{item["text"]}\n[첨부파일 {index} 끝]')
    content = ''.join(parts)
    if len(content) > MAX_INPUT_CHARS:
        raise error('이메일과 첨부파일을 합친 입력은 60,000자 이하여야 합니다. 파일 수나 내용을 줄여 주세요.', 413, 'ATTACHMENT_INPUT_TOO_LARGE')
    return content


def safe_filename(value):
    if not isinstance(value, str):
        raise error('파일 이름이 필요합니다.')
    name = value.replace('\\', '/').rsplit('/', 1)[-1]
    if not name.strip() or len(name) > 200 or any(unicodedata.category(c).startswith('C') for c in name):
        raise error('파일 이름은 제어 문자가 없는 200자 이하의 이름이어야 합니다.')
    suffix = Path(name).suffix.lower()
    if suffix in ('.doc', '.ppt'):
        raise error('구형 DOC·PPT는 DOCX·PPTX로 저장한 뒤 업로드해 주세요.', 415, 'UNSUPPORTED_FILE_TYPE')
    if suffix not in MEDIA_TYPES:
        raise error('TXT, PDF, DOCX, PPTX, PNG, JPG, JPEG, HWP, HWPX 파일을 선택해 주세요.', 415, 'UNSUPPORTED_FILE_TYPE')
    return name, MEDIA_TYPES[suffix]


class LocalStorage:
    backend = 'local'

    def __init__(self, directory):
        self.root = Path(directory).resolve()

    def _path(self, key):
        if not _OBJECT_KEY.fullmatch(key):
            raise ValueError('Invalid object key')
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError('Invalid object path')
        return path

    def put(self, path, key, media_type, sha256):
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(path, 'rb') as source, open(target, 'xb') as dest:
            os.chmod(target, 0o600)
            shutil.copyfileobj(source, dest, 1024 * 1024)

    def delete(self, key):
        self._path(key).unlink(missing_ok=True)


class S3Storage:
    backend = 's3'

    def __init__(self, endpoint, bucket, access_key, secret_key, region='auto'):
        import boto3
        from botocore.config import Config
        self.bucket = bucket
        self.client = boto3.client('s3', endpoint_url=endpoint, region_name=region,
                                  aws_access_key_id=access_key, aws_secret_access_key=secret_key,
                                  config=Config(signature_version='s3v4', connect_timeout=5, read_timeout=20,
                                                retries={'total_max_attempts': 2}, s3={'addressing_style': 'path'}))

    def put(self, path, key, media_type, sha256):
        with open(path, 'rb') as source:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=source,
                                   ContentType=media_type, Metadata={'sha256': sha256})

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)


def storage_from_env():
    mode = os.environ.get('FINCODE_ATTACHMENT_STORAGE', '').strip()
    if mode == 'local':
        directory = os.environ.get('FINCODE_ATTACHMENT_DIRECTORY', '').strip()
        if is_hosted() or not directory or not Path(directory).is_absolute():
            return None
        return LocalStorage(directory)
    if mode == 's3':
        values = [os.environ.get('FINCODE_ATTACHMENT_S3_' + name, '').strip()
                  for name in ('ENDPOINT', 'BUCKET', 'ACCESS_KEY_ID', 'SECRET_ACCESS_KEY')]
        if not all(values):
            return None
        try:
            endpoint = urlsplit(values[0])
            if endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                return None
            return S3Storage(*values, region=os.environ.get('FINCODE_ATTACHMENT_S3_REGION', 'auto'))
        except (ValueError, ImportError):
            return None
    return None


class AttachmentService:
    def __init__(self, persistence, storage=None, extractor=None):
        self.db = persistence.db
        self.storage = storage if storage is not None else storage_from_env()
        self.extractor = extractor
        self._active = None

    @property
    def configured(self):
        return self.db is not None and self.storage is not None

    @property
    def busy(self):
        return self._active is not None and not self._active.done()

    def capabilities(self):
        return {'enabled': self.configured, 'maxFileBytes': MAX_FILE_BYTES, 'maxFiles': MAX_FILES,
                'extensions': [ext[1:] for ext in MEDIA_TYPES], 'maxExtractedChars': MAX_EXTRACTED_CHARS,
                'maxInputChars': MAX_INPUT_CHARS}

    def _save_record(self, identifier, filename, media_type, size, key, sha, extraction, elapsed):
        from backend.database import insert
        metadata = {key: extraction[key] for key in ('warnings', 'pageCount') if key in extraction}
        with self.db.connect() as conn:
            insert(conn, 'attachments', {
                'id': identifier, 'email_case_id': None, 'attachment_key': identifier,
                'extraction_revision': 1, 'original_name': filename, 'media_type': media_type,
                'size_bytes': size, 'storage_key': key, 'storage_backend': self.storage.backend,
                'file_sha256': sha, 'extraction_status': 'succeeded',
                'extractor_name': extraction['method'], 'extractor_version': extraction['extractorVersion'],
                'extracted_text': extraction['text'],
                'extracted_sha256': hashlib.sha256(extraction['text'].encode('utf-8')).hexdigest(),
                'extraction_ms': elapsed, 'extraction_metadata': metadata,
            })
            return load_attachments(conn, [identifier])[0]

    def get(self, identifier):
        if self.db is None:
            raise error('첨부파일 조회에는 DB 연결이 필요합니다.', 503, 'DATABASE_NOT_CONFIGURED')
        try:
            with self.db.connect() as conn:
                return load_attachments(conn, [identifier])[0]
        except Exception as exc:
            from backend.database import DataError
            if isinstance(exc, DataError):
                raise
            raise error('첨부파일 정보를 조회하지 못했습니다.', 503, 'ATTACHMENT_DATABASE_UNAVAILABLE') from None

    def _record_exists(self, identifier):
        with self.db.connect() as conn:
            return conn.execute('SELECT id FROM attachments WHERE id=%s', (identifier,)).fetchone() is not None

    def _process(self, upload, filename, media_type):
        from backend.attachment_extract import AttachmentExtractionError, run_extraction
        extractor = self.extractor or run_extraction
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix='fincode-attachment-') as directory:
            path = Path(directory) / ('upload' + Path(filename).suffix.lower())
            sha = hashlib.sha256()
            size = 0
            with open(path, 'xb') as out:
                while chunk := upload.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise error('파일은 10 MiB 이하여야 합니다.', 413, 'UPLOAD_TOO_LARGE')
                    sha.update(chunk)
                    out.write(chunk)
            if not size:
                raise error('빈 파일은 업로드할 수 없습니다.', 400, 'EMPTY_FILE')
            try:
                extraction = extractor(str(path), filename)
            except AttachmentExtractionError as exc:
                raise error(exc.message, 422, exc.code) from None
            except Exception:
                raise error('파일의 텍스트를 추출하지 못했습니다. 파일 형식과 내용을 확인해 주세요.', 422, 'EXTRACTION_FAILED') from None
            text = extraction.get('text') if isinstance(extraction, dict) else None
            if not isinstance(text, str) or not text.strip():
                raise error('파일에서 읽을 수 있는 텍스트를 찾지 못했습니다.', 422, 'EMPTY_EXTRACTION')
            if len(text) > MAX_EXTRACTED_CHARS:
                raise error('파일의 텍스트가 50,000자를 초과합니다. 내용을 줄여 주세요.', 413, 'ATTACHMENT_TEXT_TOO_LARGE')
            identifier = str(uuid4())
            key = f'attachments/{identifier}/{sha.hexdigest()}'
            try:
                self.storage.put(path, key, media_type, sha.hexdigest())
            except Exception:
                try:
                    self.storage.delete(key)
                except Exception:
                    pass
                raise error('원본 파일을 저장하지 못했습니다. 저장소 연결을 확인해 주세요.', 503, 'ATTACHMENT_STORAGE_FAILED') from None
            try:
                return self._save_record(identifier, filename, media_type, size, key, sha.hexdigest(), extraction,
                                         max(0, round((time.perf_counter() - started) * 1000)))
            except Exception:
                # A commit acknowledgment can be lost even when the row was
                # committed. Delete only a confirmed orphan, never an original
                # that a successful attachment row may already reference.
                try:
                    if not self._record_exists(identifier):
                        self.storage.delete(key)
                except Exception:
                    pass
                raise error('첨부파일 정보를 DB에 저장하지 못했습니다.', 503, 'ATTACHMENT_SAVE_FAILED') from None

    async def process(self, upload, filename, media_type):
        if self.busy:
            await upload.close()
            raise error('다른 파일의 텍스트를 추출 중입니다. 잠시 후 다시 업로드해 주세요.', 429, 'ATTACHMENT_BUSY')

        async def complete():
            try:
                return await asyncio.to_thread(self._process, upload, filename, media_type)
            finally:
                await upload.close()

        self._active = asyncio.create_task(complete())
        # A client disconnect must not release the extraction slot while the
        # bounded parser thread is still running or close its file underneath it.
        self._active.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        return await asyncio.shield(self._active)


class _UploadTooLarge(MultiPartException):
    pass


def _origin(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            return None
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == 'https' else 80)
    except ValueError:
        return None


def install_routes(app, persistence, *, storage=None, extractor=None):
    service = AttachmentService(persistence, storage, extractor)

    @app.post('/api/attachments', status_code=201)
    async def upload(request: Request):
        origin = request.headers.get('origin')
        if origin is not None and (_origin(origin) is None or _origin(origin) != _origin(f'{request.url.scheme}://{request.url.netloc}')):
            raise error('같은 대시보드에서 파일을 업로드해 주세요.', 403, 'ORIGIN_REJECTED')
        if not service.configured:
            raise error('첨부파일 저장소와 DB 설정이 필요합니다.', 503, 'ATTACHMENTS_NOT_CONFIGURED')
        if service.busy:
            raise error('다른 파일의 텍스트를 추출 중입니다. 잠시 후 다시 업로드해 주세요.', 429, 'ATTACHMENT_BUSY')
        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'multipart/form-data':
            raise error('파일은 multipart/form-data 형식으로 업로드해 주세요.')
        length = request.headers.get('content-length')
        if length is not None:
            if not length.isascii() or not length.isdecimal():
                raise error('요청 크기가 올바르지 않습니다.')
            # Compare decimal strings so an arbitrarily long header cannot
            # trigger Python's integer conversion limit and become a 500.
            normalized_length = length.lstrip('0') or '0'
            maximum_length = str(MAX_REQUEST_BYTES)
            if (len(normalized_length) > len(maximum_length)
                    or (len(normalized_length) == len(maximum_length) and normalized_length > maximum_length)):
                raise error('파일은 10 MiB 이하여야 합니다.', 413, 'UPLOAD_TOO_LARGE')

        async def bounded_stream():
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_REQUEST_BYTES:
                    raise _UploadTooLarge('request too large')
                yield chunk

        form = None
        try:
            parser = MultiPartParser(request.headers, bounded_stream(), max_files=1, max_fields=0,
                                     max_part_size=MAX_FILE_BYTES)
            try:
                async with asyncio.timeout(30):
                    form = await parser.parse()
            except BaseException:
                # Starlette versions differ in whether their exception cleanup
                # includes stream cancellation and multipart decoder failures.
                for opened_file in getattr(parser, '_files_to_close_on_error', []):
                    opened_file.close()
                raise
            entries = form.multi_items()
            if len(entries) != 1 or entries[0][0] != 'file' or not isinstance(entries[0][1], UploadFile):
                raise error('file 항목으로 파일 하나를 업로드해 주세요.')
            uploaded = entries[0][1]
            if uploaded.size is not None and uploaded.size > MAX_FILE_BYTES:
                raise error('파일은 10 MiB 이하여야 합니다.', 413, 'UPLOAD_TOO_LARGE')
            filename, media_type = safe_filename(uploaded.filename)
            # process() owns the UploadFile until its worker completes, including
            # when the HTTP request is cancelled.
            form = None
            return {'attachment': await service.process(uploaded, filename, media_type)}
        except _UploadTooLarge:
            raise error('파일은 10 MiB 이하여야 합니다.', 413, 'UPLOAD_TOO_LARGE') from None
        except MultiPartException:
            raise error('파일 하나만 포함한 올바른 업로드 요청을 보내 주세요.') from None
        except MultipartParseError:
            raise error('파일 업로드 요청의 구분 형식이 올바르지 않습니다.', 400, 'INVALID_MULTIPART') from None
        except TimeoutError:
            raise error('파일 업로드 시간이 초과되었습니다. 다시 업로드해 주세요.', 408, 'UPLOAD_TIMEOUT') from None
        finally:
            if form is not None:
                await form.close()

    @app.get('/api/attachments/{identifier}')
    async def attachment(identifier: str):
        return {'attachment': await asyncio.to_thread(service.get, identifier)}

    return service
