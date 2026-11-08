"""HTML fragments are indexed as markup (ADR 0155); a file whose bytes are not
its format is an outcome of those bytes, not an ingest failure (ADR 0156)."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imprint.extractors import ExtractionError, UnreadableDocument, dispatch_by_ext, html, image, pdf


class MalformedFileTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def write(self, name, data):
        path = self.folder / name
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding='utf-8', newline='')  # As given, on Windows too.
        return str(path)


class PdfTests(MalformedFileTests):
    def test_bytes_that_are_not_a_pdf_are_unreadable_and_log_nothing(self):
        for name, data, cause in [('fixture.pdf', b'test\n', 'Stream has ended unexpectedly'),
                                  ('empty.pdf', b'', 'Cannot read an empty file')]:
            with self.subTest(name=name), self.assertNoLogs('pypdf', level='WARNING'):
                with self.assertRaisesRegex(UnreadableDocument, f'not a readable PDF: {cause}'):
                    dispatch_by_ext(self.write(name, data))

    def test_a_password_protected_pdf_is_unreadable(self):
        import pypdf
        writer = pypdf.PdfWriter()
        writer.add_blank_page(200, 200)
        writer.encrypt(user_password='secret', owner_password='owner', algorithm='RC4-128')
        out = io.BytesIO()
        writer.write(out)
        with self.assertRaisesRegex(UnreadableDocument, 'encrypted PDF: it opens only with its password'):
            pdf._extract_bytes(out.getvalue())

    def test_other_parser_errors_still_fail(self):
        with patch('pypdf.PdfReader', side_effect=KeyError('/Root')):
            with self.assertRaises(ExtractionError) as raised:
                pdf._extract_bytes(b'%PDF-1.7 a reader bug')
        self.assertNotIsInstance(raised.exception, UnreadableDocument)


class ImageTests(MalformedFileTests):
    def setUp(self):
        super().setUp()
        enabled = patch.object(image, '_ocr_enabled', return_value=True)
        enabled.start()
        self.addCleanup(enabled.stop)

    def png(self):
        from PIL import Image
        out = io.BytesIO()
        Image.new('RGB', (64, 64), 'white').save(out, format='PNG')
        return out.getvalue()

    def test_text_or_nothing_under_an_image_name_is_unreadable(self):
        for name, data in [('test-txt-as-png.png', b'I am not an image\n'), ('icon.png', b'')]:
            with self.subTest(name=name), self.assertRaises(UnreadableDocument) as raised:
                dispatch_by_ext(self.write(name, data))
            self.assertEqual(str(raised.exception), 'not a readable image: unrecognized format')

    def test_a_truncated_image_is_unreadable(self):
        with self.assertRaisesRegex(UnreadableDocument, 'not a readable image'):
            image._extract_bytes(self.png()[:80])

    def test_ocr_errors_on_a_valid_image_are_not_taken_for_damage(self):
        with patch('imprint.extractors.ocr.recognize', side_effect=RuntimeError('onnx session failed')):
            with self.assertRaisesRegex(RuntimeError, 'onnx session failed') as raised:
                image._extract_bytes(self.png())
        self.assertNotIsInstance(raised.exception, UnreadableDocument)


class HtmlTests(MalformedFileTests):
    def test_fragments_are_indexed_verbatim_as_markup(self):
        for name, text in [
            ('star.component.html', '<account-sw-icon\n  icon="star"\n  [size]="size"\n  [class.disabled]="disabled" />\n'),
            ('list.component.html', '<div *ngIf="items.length">{{ \'shop.list.title\' | translate }}</div>\n'),
            ('pseudo-modal.template.html', '<div class="modal"><h5 class="modal-title"></h5></div>\n'),
            ('nav.html', '<header class="top"><nav>Home</nav></header>\n'),
        ]:
            with self.subTest(name=name):
                doc, = dispatch_by_ext(self.write(name, text))
                self.assertEqual(doc.text, text)
                self.assertEqual(doc.chunk_mode, 'code')
                self.assertEqual(doc.metadata['source_format'], 'html_fragment')

    def test_a_page_without_text_has_nothing_to_index(self):
        shell = ('<!DOCTYPE html>\n<html>\n<head><script type="module" src="https://unpkg.com/rapidoc/dist/rapidoc-min.js">'
                 '</script></head>\n<body><rapi-doc spec-url="http://localhost:8000/api/doc/default.json"></rapi-doc>'
                 '</body>\n</html>\n')
        doc, = dispatch_by_ext(self.write('index.html', shell))
        self.assertEqual(doc.text, '')

    def test_a_page_with_text_is_read_as_prose(self):
        page = ('<!doctype html><html><head><title>Release notes</title></head><body><article><h1>Release notes</h1>'
                + '<p>The invoice export now includes the tax number of every company on the document.</p>' * 3
                + '</article></body></html>')
        doc, = dispatch_by_ext(self.write('notes.html', page))
        self.assertIn('tax number', doc.text)
        self.assertEqual(doc.chunk_mode, 'prose')

    def test_a_downloaded_page_without_text_is_still_an_error(self):
        with self.assertRaisesRegex(ExtractionError, 'empty output'):
            html._extract_bytes(b'<html><body><div id="app"></div></body></html>', 'https://example.com')


if __name__ == '__main__':
    unittest.main()
