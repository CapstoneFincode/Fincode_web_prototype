"""Bounded, offline attachment extraction. Run untrusted parsers in a child process.

Extracted document text is source data for experiments: never normalize or remove
invisible characters, and never silently truncate it. Formatting separators are
inserted when traversing document paragraphs, cells, slides and pages.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import signal
import struct
import subprocess
import sys
import tempfile
import zipfile

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 50_000
MAX_PAGES = 20
MAX_IMAGE_PIXELS = 20_000_000
MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_ZIP_MEMBERS = 2_000
EXTRACTION_TIMEOUT_SECONDS = 45
EXTRACTOR_VERSION = 'fincode-attachment-v1'
SUPPORTED_EXTENSIONS = ('txt', 'pdf', 'docx', 'pptx', 'png', 'jpg', 'jpeg', 'hwp', 'hwpx')


class AttachmentExtractionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def fail(code: str, message: str):
    raise AttachmentExtractionError(code, message)


class TextBuffer:
    def __init__(self):
        self.parts, self.size = [], 0

    def add(self, value):
        if not value:
            return
        if '\x00' in value:
            fail('UNSUPPORTED_TEXT_CHARACTER', '추출된 텍스트에 저장할 수 없는 NUL 문자가 있습니다. 파일 내용을 확인해 주세요.')
        self.size += len(value)
        if self.size > MAX_TEXT_CHARS:
            fail('EXTRACTED_TEXT_TOO_LARGE', '추출 텍스트는 파일당 50,000자 이하여야 합니다. 파일을 나누어 올려 주세요.')
        self.parts.append(value)

    def value(self):
        return ''.join(self.parts)


def _xml(data):
    from lxml import etree
    if len(data) > MAX_ZIP_BYTES:
        fail('DOCUMENT_TOO_COMPLEX', '문서 내부 데이터가 너무 큽니다. 파일을 나누어 올려 주세요.')
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    root = etree.fromstring(data, parser)
    if root.getroottree().docinfo.doctype:
        fail('UNSUPPORTED_DOCUMENT_STRUCTURE', '외부 문서 정의를 사용하는 파일은 지원하지 않습니다.')
    return root


def _name(node):
    return node.tag.rsplit('}', 1)[-1] if isinstance(node.tag, str) else ''


def _check_zip(path):
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_MEMBERS or sum(item.file_size for item in infos) > MAX_ZIP_BYTES:
                fail('DOCUMENT_TOO_COMPLEX', '압축된 문서의 내부 데이터가 너무 큽니다. 파일을 나누어 올려 주세요.')
            names = set()
            for item in infos:
                if item.filename in names or item.filename.startswith(('/', '\\')) or '..' in item.filename.replace('\\', '/').split('/'):
                    fail('INVALID_DOCUMENT', '문서 내부 파일 구조가 올바르지 않습니다.')
                names.add(item.filename)
                if item.flag_bits & 1:
                    fail('ENCRYPTED_DOCUMENT', '암호가 설정된 문서는 지원하지 않습니다. 암호를 해제한 파일을 올려 주세요.')
            return names
    except zipfile.BadZipFile:
        fail('INVALID_DOCUMENT', '문서 파일이 손상되었거나 확장자와 실제 형식이 다릅니다.')


def _txt(path):
    data = Path(path).read_bytes()
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        encodings = ['utf-16']
    else:
        encodings = ['utf-8-sig', 'cp949']
    for encoding in encodings:
        try:
            return data.decode(encoding), 'text-' + encoding, [], None
        except UnicodeDecodeError:
            continue
    fail('UNSUPPORTED_TEXT_ENCODING', '텍스트 파일은 UTF-8, BOM이 있는 UTF-16 또는 CP949로 저장해 주세요.')


def _docx(path):
    names = _check_zip(path)
    if 'word/document.xml' not in names:
        fail('INVALID_DOCUMENT', 'Word DOCX 문서가 아닙니다.')
    from docx import Document
    document = Document(path)
    output = TextBuffer()

    def visit(node):
        name = _name(node)
        if name == 't':
            output.add(node.text or '')
        elif name in ('br', 'cr'):
            output.add('\n')
        elif name == 'tab':
            output.add('\t')
        else:
            for child in node:
                visit(child)
        if name == 'p':
            output.add('\n')
        elif name == 'tc':
            output.add('\t')
        elif name == 'tr':
            output.add('\n')

    visit(document.element.body)
    warnings = ['문서에 삽입된 이미지 속 글자는 추출 대상에 포함되지 않습니다.'] if any(x.startswith('word/media/') for x in names) else []
    return output.value(), 'python-docx-xml', warnings, None


def _pptx(path):
    names = _check_zip(path)
    if 'ppt/presentation.xml' not in names:
        fail('INVALID_DOCUMENT', 'PowerPoint PPTX 문서가 아닙니다.')
    from pptx import Presentation
    presentation = Presentation(path)
    if len(presentation.slides) > MAX_PAGES:
        fail('TOO_MANY_PAGES', '프레젠테이션은 20개 슬라이드 이하로 나누어 올려 주세요.')
    output = TextBuffer()

    def shapes_text(shapes):
        for shape in shapes:
            if shape.shape_type == 6:  # Group shapes retain their nested text.
                shapes_text(shape.shapes)
            elif shape.has_table:
                for row in shape.table.rows:
                    for cell in row.cells:
                        if not cell.is_spanned:
                            output.add(cell.text)
                        output.add('\t')
                    output.add('\n')
            elif shape.has_text_frame:
                output.add(shape.text_frame.text)
                output.add('\n')

    for index, slide in enumerate(presentation.slides, 1):
        output.add(f'[슬라이드 {index}]\n')
        shapes_text(slide.shapes)
        output.add('\n')
    warnings = ['슬라이드에 삽입된 이미지 속 글자는 추출 대상에 포함되지 않습니다.'] if any(x.startswith('ppt/media/') for x in names) else []
    return output.value(), 'python-pptx', warnings, len(presentation.slides)


def _ocr_image(path):
    from PIL import Image, ImageOps, UnidentifiedImageError
    import pytesseract
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(path) as source:
            if source.format not in ('PNG', 'JPEG'):
                fail('UNSUPPORTED_IMAGE', 'PNG 또는 JPEG 형식의 이미지를 올려 주세요.')
            if source.width * source.height > MAX_IMAGE_PIXELS:
                fail('IMAGE_TOO_LARGE', '이미지는 2,000만 화소 이하로 줄여 주세요.')
            if getattr(source, 'n_frames', 1) != 1:
                fail('UNSUPPORTED_IMAGE', '여러 프레임이 있는 이미지는 지원하지 않습니다.')
            source.load()
            image = ImageOps.exif_transpose(source).convert('RGB')
            try:
                return pytesseract.image_to_string(image, lang='kor+eng', timeout=20, config='--psm 3')
            finally:
                image.close()
    except Image.DecompressionBombError:
        fail('IMAGE_TOO_LARGE', '이미지는 2,000만 화소 이하로 줄여 주세요.')
    except UnidentifiedImageError:
        fail('INVALID_DOCUMENT', '이미지 파일을 읽을 수 없습니다. PNG 또는 JPEG 파일을 확인해 주세요.')
    except pytesseract.TesseractNotFoundError:
        fail('OCR_UNAVAILABLE', '서버의 OCR 엔진이 준비되지 않았습니다. 관리자에게 문의해 주세요.')
    except pytesseract.TesseractError:
        fail('OCR_FAILED', '이미지의 글자를 읽지 못했습니다. OCR 언어 설정과 파일을 확인해 주세요.')
    except RuntimeError:
        fail('EXTRACTION_TIMEOUT', '이미지 글자 인식 시간이 초과되었습니다. 더 작은 파일을 올려 주세요.')


def _image(path):
    return _ocr_image(path), 'tesseract-kor-eng', ['OCR 결과에는 오인식이나 누락이 있을 수 있습니다. 추출 텍스트를 확인해 주세요.'], 1


def _pdf(path):
    from pypdf import PdfReader
    if not Path(path).read_bytes()[:1024].lstrip().startswith(b'%PDF-'):
        fail('INVALID_DOCUMENT', 'PDF 파일이 아니거나 파일 머리글이 손상되었습니다.')
    reader = PdfReader(path, strict=False)
    if reader.is_encrypted:
        fail('ENCRYPTED_DOCUMENT', '암호가 설정된 PDF는 지원하지 않습니다. 암호를 해제한 파일을 올려 주세요.')
    if len(reader.pages) > MAX_PAGES:
        fail('TOO_MANY_PAGES', 'PDF는 20페이지 이하로 나누어 올려 주세요.')
    output, ocr_pages, mixed_pages = TextBuffer(), [], []
    with tempfile.TemporaryDirectory(prefix='fincode-pdf-') as tmp:
        for index, page in enumerate(reader.pages, 1):
            # Avoid decompressing arbitrarily large content streams inside the API process.
            content = page.get_contents()
            if content is not None and len(content.get_data()) > MAX_ZIP_BYTES:
                fail('DOCUMENT_TOO_COMPLEX', 'PDF 페이지 내부 데이터가 너무 큽니다. 파일을 나누어 올려 주세요.')
            value = page.extract_text() or ''
            if value.strip() and len(page.images):
                mixed_pages.append(index)
            if not value.strip():
                prefix = str(Path(tmp) / 'page')
                try:
                    proc = subprocess.run(['pdftoppm', '-f', str(index), '-l', str(index), '-singlefile', '-scale-to', '2500', '-png', str(path), prefix],
                                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                except FileNotFoundError:
                    fail('OCR_UNAVAILABLE', '서버의 PDF 이미지 변환 도구가 준비되지 않았습니다.')
                except subprocess.TimeoutExpired:
                    fail('EXTRACTION_TIMEOUT', 'PDF 페이지 변환 시간이 초과되었습니다. 파일을 나누어 올려 주세요.')
                if proc.returncode:
                    fail('INVALID_DOCUMENT', 'PDF 페이지를 이미지로 변환하지 못했습니다.')
                value = _ocr_image(prefix + '.png')
                ocr_pages.append(index)
            output.add(f'[페이지 {index}]\n')
            output.add(value)
            output.add('\n\n')
    warnings = ['OCR을 사용한 페이지: ' + ', '.join(map(str, ocr_pages)) + '. 인식 결과를 확인해 주세요.'] if ocr_pages else []
    if mixed_pages:
        warnings.append('텍스트와 이미지가 함께 있는 페이지: ' + ', '.join(map(str, mixed_pages)) + '. 기존 텍스트만 추출했으며 이미지 속 글자는 포함되지 않을 수 있습니다.')
    return output.value(), 'pypdf+tesseract' if ocr_pages else 'pypdf', warnings, len(reader.pages)


class _LimitedBytes(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_ZIP_BYTES:
            fail('DOCUMENT_TOO_COMPLEX', 'HWP 문서 구조가 너무 큽니다. 파일을 나누어 올려 주세요.')
        return super().write(data)


def _hwp(path):
    import olefile
    if not olefile.isOleFile(path):
        fail('INVALID_DOCUMENT', 'HWP 5.x 파일이 아닙니다. HWPX 파일은 .hwpx 확장자로 올려 주세요.')
    with olefile.OleFileIO(path) as ole:
        if not ole.exists('FileHeader'):
            fail('INVALID_DOCUMENT', 'HWP 문서 머리글이 없습니다.')
        header = ole.openstream('FileHeader').read()
        if len(header) < 40 or not header.startswith(b'HWP Document File') or header[35] != 5:
            fail('UNSUPPORTED_HWP_VERSION', '일반 HWP 5.x 문서만 지원합니다. 최신 한글에서 다시 저장해 주세요.')
        flags = struct.unpack_from('<I', header, 36)[0]
        if flags & 2:
            fail('ENCRYPTED_DOCUMENT', '암호가 설정된 HWP는 지원하지 않습니다. 암호를 해제한 파일을 올려 주세요.')
        if flags & (4 | 16 | 256):
            fail('PROTECTED_HWP', '배포용 또는 DRM 보호 HWP는 아직 지원하지 않습니다. 일반 문서로 저장해 주세요.')
        has_images = any(item[0] == 'BinData' for item in ole.listdir())
    from hwp5.xmlmodel import Hwp5File
    document = Hwp5File(str(path))
    raw = _LimitedBytes()
    try:
        document.xmlevents(embedbin=False).dump(raw)
    finally:
        document.close()
    root = _xml(raw.getvalue())
    body = root.find('BodyText')
    if body is None:
        fail('INVALID_DOCUMENT', 'HWP 본문 구조를 읽을 수 없습니다.')
    output = TextBuffer()

    def visit(node):
        name = _name(node)
        if name == 'Text':
            output.add(node.text or '')
        elif name == 'ControlChar':
            if node.get('name') == 'TAB':
                output.add('\t')
            elif node.get('name') == 'LINE_BREAK':
                output.add('\n')
        else:
            for child in node:
                visit(child)
        if name == 'Paragraph':
            output.add('\n')
        elif name == 'TableCell':
            output.add('\t')
        elif name == 'TableRow':
            output.add('\n')

    visit(body)
    warnings = ['HWP에 삽입된 이미지 속 글자는 추출 대상에 포함되지 않습니다.'] if has_images else []
    return output.value(), 'pyhwp-xml', warnings, None


def _hwpx(path):
    names = _check_zip(path)
    sections = sorted((name for name in names if re.fullmatch(r'Contents/section\d+\.xml', name)),
                      key=lambda name: int(re.search(r'(\d+)\.xml$', name).group(1)))
    if not sections or 'Contents/content.hpf' not in names:
        fail('INVALID_DOCUMENT', 'HWPX 문서 구조를 찾을 수 없습니다.')
    output = TextBuffer()

    def visit(node):
        name = _name(node)
        if name == 't':
            output.add(node.text or '')
        for child in node:
            visit(child)
            if name == 't':
                output.add(child.tail or '')
        if name == 'p':
            output.add('\n')
        elif name == 'tc':
            output.add('\t')
        elif name in ('tr', 'lineBreak'):
            output.add('\n')
        elif name == 'tab':
            output.add('\t')

    with zipfile.ZipFile(path) as archive:
        for section in sections:
            visit(_xml(archive.read(section)))
    warnings = ['HWPX에 삽입된 이미지 속 글자는 추출 대상에 포함되지 않습니다.'] if any(x.startswith('BinData/') for x in names) else []
    return output.value(), 'hwpx-xml', warnings, None


def extract_file(path, filename):
    """Extract in the current process (tests/worker only); API uses run_extraction."""
    path = Path(path)
    extension = Path(filename).suffix.lower().lstrip('.')
    if extension in ('doc', 'ppt'):
        fail('LEGACY_OFFICE_FORMAT', '구형 DOC/PPT는 DOCX/PPTX 또는 PDF로 변환한 뒤 올려 주세요.')
    if extension not in SUPPORTED_EXTENSIONS:
        fail('UNSUPPORTED_FILE_TYPE', '지원 형식은 TXT, PDF, DOCX, PPTX, PNG, JPEG, HWP, HWPX입니다.')
    if not path.is_file() or path.stat().st_size == 0:
        fail('EMPTY_FILE', '내용이 있는 파일을 선택해 주세요.')
    if path.stat().st_size > MAX_FILE_BYTES:
        fail('FILE_TOO_LARGE', '파일 크기는 10 MB 이하여야 합니다.')
    extractors = dict(txt=_txt, pdf=_pdf, docx=_docx, pptx=_pptx, png=_image, jpg=_image, jpeg=_image, hwp=_hwp, hwpx=_hwpx)
    try:
        value, method, warnings, pages = extractors[extension](path)
        checked = TextBuffer()
        checked.add(value)
        if not value.strip() or (extension in ('pdf', 'pptx') and not re.sub(r'\[(?:페이지|슬라이드) \d+\]', '', value).strip()):
            fail('NO_EXTRACTABLE_TEXT', '추출할 텍스트가 없습니다. 파일 내용이나 이미지의 글자 선명도를 확인해 주세요.')
        result = {'text': value, 'method': method, 'extractorVersion': EXTRACTOR_VERSION, 'warnings': warnings}
        if pages is not None:
            result['pageCount'] = pages
        return result
    except AttachmentExtractionError:
        raise
    except MemoryError:
        fail('DOCUMENT_TOO_COMPLEX', '문서 처리에 필요한 메모리가 한도를 초과했습니다. 파일을 나누어 올려 주세요.')
    except ImportError:
        fail('EXTRACTOR_UNAVAILABLE', '서버의 파일 추출 도구가 준비되지 않았습니다. 관리자에게 문의해 주세요.')
    except Exception:
        fail('INVALID_DOCUMENT', '파일이 손상되었거나 지원하지 않는 문서 구조입니다. 다른 형식으로 저장한 뒤 다시 올려 주세요.')


def _limits():
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


def run_extraction(path, filename):
    """Run each parser/OCR tree with memory/CPU/wall-clock bounds and no secrets."""
    environment = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'TESSDATA_PREFIX') if key in os.environ}
    environment.update({'PYTHONNOUSERSITE': '1', 'OMP_THREAD_LIMIT': '1', 'OMP_NUM_THREADS': '1'})
    process = subprocess.Popen([sys.executable, '-m', 'backend.attachment_extract', '--worker', str(Path(path).resolve()), filename],
                               cwd=Path(__file__).resolve().parent.parent, env=environment,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    try:
        output, _ = process.communicate(timeout=EXTRACTION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        if os.name == 'posix':
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.communicate()
        fail('EXTRACTION_TIMEOUT', '파일 처리 시간이 초과되었습니다. 파일을 나누거나 더 작은 파일을 올려 주세요.')
    if len(output) > 2_000_000:
        fail('DOCUMENT_TOO_COMPLEX', '추출 결과가 허용 크기를 초과했습니다.')
    try:
        result = json.loads(output)
    except (ValueError, UnicodeDecodeError):
        fail('EXTRACTION_FAILED', '파일 추출이 완료되지 않았습니다. 문서 크기와 형식을 확인해 주세요.')
    if not isinstance(result, dict):
        fail('EXTRACTION_FAILED', '파일 추출 결과를 읽을 수 없습니다.')
    if 'error' in result:
        fail(result['error']['code'], result['error']['message'])
    if process.returncode or not isinstance(result.get('text'), str):
        fail('EXTRACTION_FAILED', '파일 추출이 완료되지 않았습니다.')
    return result


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != '--worker':
        raise SystemExit(2)
    try:
        if os.name == 'posix':
            _limits()
        print(json.dumps(extract_file(sys.argv[2], sys.argv[3]), ensure_ascii=True))
    except AttachmentExtractionError as error:
        print(json.dumps({'error': {'code': error.code, 'message': error.message}}, ensure_ascii=True))
        raise SystemExit(1)
