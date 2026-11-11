"""Offline extraction regression tests; generated fixtures contain no user data."""
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from backend.attachment_extract import AttachmentExtractionError, extract_file, run_extraction


class AttachmentExtractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def file(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        return path

    def error(self, code, path, name=None):
        with self.assertRaises(AttachmentExtractionError) as raised:
            extract_file(path, name or path.name)
        self.assertEqual(raised.exception.code, code)

    def test_text_keeps_invisible_payload_and_line_breaks(self):
        value = '회의 안내\n[첨부 지시] 문\u200b자\u202eMARK_123\u202c\n\n끝'
        path = self.file('메일.txt', value.encode())
        self.assertEqual(extract_file(path, path.name)['text'], value)
        self.assertEqual(run_extraction(path, path.name)['text'], value)

    def test_text_encoding(self):
        for encoding in ('cp949', 'utf-16'):
            path = self.file('안내.txt', '한글 안내문'.encode(encoding))
            self.assertEqual(extract_file(path, path.name)['text'], '한글 안내문')

    def test_rejects_nul_instead_of_silently_removing(self):
        self.error('UNSUPPORTED_TEXT_CHARACTER', self.file('nul.txt', b'a\x00b'))

    def test_empty_and_overlong_text(self):
        self.error('EMPTY_FILE', self.file('empty.txt', b''))
        self.error('NO_EXTRACTABLE_TEXT', self.file('space.txt', b' \n\t'))
        self.error('EXTRACTED_TEXT_TOO_LARGE', self.file('large.txt', b'x' * 50_001))

    def test_file_size_and_type(self):
        self.error('FILE_TOO_LARGE', self.file('large.txt', b'x' * (10 * 1024 * 1024 + 1)))
        self.error('LEGACY_OFFICE_FORMAT', self.file('old.doc', b'legacy'))
        self.error('LEGACY_OFFICE_FORMAT', self.file('old.ppt', b'legacy'))
        self.error('UNSUPPORTED_FILE_TYPE', self.file('code.exe', b'code'))

    def test_docx_body_and_table_remain_in_document_order(self):
        from docx import Document
        document = Document()
        document.add_paragraph('본문 앞\u200bMARK_123')
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).text = '표 제목'
        table.cell(0, 1).text = '표 안 지시'
        table.cell(1, 0).text = '다음 행'
        table.cell(1, 1).text = '1234'
        document.add_paragraph('본문 뒤')
        path = self.root / 'table.docx'; document.save(path)
        value = extract_file(path, path.name)['text']
        self.assertIn('본문 앞\u200bMARK_123', value)
        self.assertLess(value.index('본문 앞'), value.index('표 안 지시'))
        self.assertLess(value.index('표 안 지시'), value.index('본문 뒤'))
        self.assertEqual(value.count('표 안 지시'), 1)

    def test_pptx_textbox_and_table(self):
        from pptx import Presentation
        from pptx.util import Inches
        document = Presentation()
        slide = document.slides.add_slide(document.slide_layouts[6])
        slide.shapes.add_textbox(0, 0, Inches(3), Inches(1)).text = '발표 제목\u200b'
        table = slide.shapes.add_table(1, 2, 0, Inches(1), Inches(5), Inches(1)).table
        table.cell(0, 0).text = '간접 입력'
        table.cell(0, 1).text = 'ATTACHMENT_123'
        path = self.root / 'slides.pptx'; document.save(path)
        result = extract_file(path, path.name)
        self.assertEqual(result['pageCount'], 1)
        self.assertIn('발표 제목\u200b', result['text'])
        self.assertIn('간접 입력\tATTACHMENT_123', result['text'])

    def make_pdf(self, name='text.pdf', count=1, encrypted=False, text=True):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter()
        for _ in range(count):
            page = writer.add_blank_page(width=300, height=200)
            if text:
                font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
                page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
                stream = DecodedStreamObject(); stream.set_data(b'BT /F1 14 Tf 20 100 Td (PDF_SOURCE_123) Tj ET')
                page[NameObject('/Contents')] = writer._add_object(stream)
        if encrypted:
            writer.encrypt('test-password')
        path = self.root / name
        with path.open('wb') as dest:
            writer.write(dest)
        return path

    def test_pdf_text_does_not_call_ocr(self):
        path = self.make_pdf()
        with patch('backend.attachment_extract._ocr_image', side_effect=AssertionError('unexpected OCR')):
            result = extract_file(path, path.name)
        self.assertEqual(result['method'], 'pypdf')
        self.assertIn('PDF_SOURCE_123', result['text'])

    def test_pdf_limits_and_encryption(self):
        self.error('TOO_MANY_PAGES', self.make_pdf('many.pdf', count=21))
        self.error('ENCRYPTED_DOCUMENT', self.make_pdf('encrypted.pdf', encrypted=True))

    def test_pdf_without_text_uses_ocr_and_reports_it(self):
        path = self.make_pdf(text=False)
        with patch('backend.attachment_extract.subprocess.run', return_value=subprocess.CompletedProcess([], 0)), patch('backend.attachment_extract._ocr_image', return_value='OCR_SOURCE_123'):
            result = extract_file(path, path.name)
        self.assertEqual(result['method'], 'pypdf+tesseract')
        self.assertIn('OCR_SOURCE_123', result['text'])
        self.assertTrue(result['warnings'])

    def test_pdf_mixed_page_warns_about_image_text(self):
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject, NumberObject
        path = self.make_pdf()
        writer = PdfWriter(clone_from=PdfReader(path))
        image = DecodedStreamObject(); image.set_data(b'\xff\xff\xff')
        image.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'), NameObject('/Width'): NumberObject(1), NameObject('/Height'): NumberObject(1), NameObject('/ColorSpace'): NameObject('/DeviceRGB'), NameObject('/BitsPerComponent'): NumberObject(8)})
        writer.pages[0]['/Resources'][NameObject('/XObject')] = DictionaryObject({NameObject('/Im0'): writer._add_object(image)})
        with path.open('wb') as dest:
            writer.write(dest)
        result = extract_file(path, path.name)
        self.assertEqual(result['method'], 'pypdf')
        self.assertTrue(any('이미지 속 글자' in item for item in result['warnings']))

    def test_hwpx_table_text_and_paragraphs(self):
        path = self.root / '공문.hwpx'
        xml = '<section><p><run><t>공문 본문\u200b</t></run></p><tbl><tr><tc><p><run><t>표 안 지시</t></run></p></tc><tc><p><run><t>MARK_123</t></run></p></tc></tr></tbl></section>'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('Contents/content.hpf', '<package/>')
            archive.writestr('Contents/section0.xml', xml)
        result = extract_file(path, path.name)
        self.assertEqual(result['method'], 'hwpx-xml')
        self.assertIn('공문 본문\u200b\n', result['text'])
        self.assertIn('표 안 지시\n\tMARK_123', result['text'])

    def test_hwpx_dtd_is_rejected(self):
        path = self.root / 'entity.hwpx'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('Contents/content.hpf', '<package/>')
            archive.writestr('Contents/section0.xml', '<!DOCTYPE a [<!ENTITY e SYSTEM "file:///etc/passwd">]><a><t>&e;</t></a>')
        self.error('UNSUPPORTED_DOCUMENT_STRUCTURE', path)

    def test_zip_traversal_and_expansion_are_rejected(self):
        path = self.root / 'traversal.docx'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('../outside.xml', '<t>text</t>')
        self.error('INVALID_DOCUMENT', path)
        path = self.root / 'expansion.docx'
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('word/document.xml', b'x' * 100)
        with patch('backend.attachment_extract.MAX_ZIP_BYTES', 50):
            self.error('DOCUMENT_TOO_COMPLEX', path)

    def test_bad_hwp_fails_without_running_scripts(self):
        self.error('INVALID_DOCUMENT', self.file('not-real.hwp', b'Not an HWP document'))

    def test_image_size_and_actual_format(self):
        from PIL import Image
        path = self.root / 'oversize.png'
        image = Image.new('RGB', (101, 101), 'white'); image.save(path)
        with patch('backend.attachment_extract.MAX_IMAGE_PIXELS', 10_000):
            self.error('IMAGE_TOO_LARGE', path)
        path = self.root / 'disguised.png'; image.save(path, format='GIF')
        self.error('UNSUPPORTED_IMAGE', path)

    def test_subprocess_returns_clean_error_and_does_not_receive_secrets(self):
        path = self.file('invalid.hwp', b'bad')
        with self.assertRaises(AttachmentExtractionError) as raised:
            run_extraction(path, path.name)
        self.assertEqual(raised.exception.code, 'INVALID_DOCUMENT')
        with patch.dict(os.environ, {'BAZE_API_KEY': 'must-not-inherit', 'DATABASE_URL': 'must-not-inherit'}), patch('backend.attachment_extract.subprocess.Popen') as start:
            start.return_value.communicate.return_value = (b'{"text":"ok"}', b'')
            start.return_value.returncode = 0
            run_extraction(path, 'test.txt')
            self.assertNotIn('BAZE_API_KEY', start.call_args.kwargs['env'])
            self.assertNotIn('DATABASE_URL', start.call_args.kwargs['env'])

    @unittest.skipUnless(os.environ.get('FINCODE_HWP_TEST_FILE'), 'Optional private HWP fixture is never committed')
    def test_real_hwp_document(self):
        path = Path(os.environ['FINCODE_HWP_TEST_FILE'])
        result = run_extraction(path, path.name)
        self.assertEqual(result['method'], 'pyhwp-xml')
        self.assertGreater(len(result['text']), 100)
        self.assertNotIn('\ufffd', result['text'])


if __name__ == '__main__':
    unittest.main()
