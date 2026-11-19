"""Default OCR configuration and failures must not become cached empty scans."""
import os
import tempfile
import unittest
from unittest.mock import patch

from imprint.extractors import ExtractionError, ExtractorUnavailable
from imprint.extractors import image, pdf
from imprint import config_schema, source_state


class OcrRuntimeTests(unittest.TestCase):
    def test_default_enabled_and_explicit_opt_out_preserved(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'IMPRINT_DATA_DIR': directory}):
            with patch.dict(os.environ, {'IMPRINT_INGEST_OCR': 'true'}):
                self.assertTrue(image._ocr_enabled())
            with patch.dict(os.environ, {'IMPRINT_INGEST_OCR': 'false'}):
                self.assertFalse(image._ocr_enabled())
                with self.assertRaises(ExtractorUnavailable):
                    image._extract_bytes(b'not read while disabled')
        self.assertTrue(next(s.default for s in config_schema.SETTINGS if s.key == 'ingest.ocr_enabled'))

    def test_a_missing_runtime_names_the_import_that_failed(self):
        from imprint.extractors import ocr
        ocr._engine.cache_clear()
        self.addCleanup(ocr._engine.cache_clear)
        with patch.dict('sys.modules', {'rapidocr': None}), \
                self.assertRaisesRegex(ExtractorUnavailable, 'rapidocr'):
            ocr._engine()
        with patch.object(image, '_ocr_enabled', return_value=True), patch.dict('sys.modules', {'PIL': None}), \
                self.assertRaisesRegex(ExtractorUnavailable, 'PIL'):
            image._extract_bytes(b'not read without Pillow')

    def test_pdf_ocr_errors_propagate_and_text_layer_skips_ocr(self):
        with patch.object(pdf, '_ocr_enabled', return_value=True), \
                patch.object(pdf, '_extract_text_pypdf', return_value=('', {'page_count': 1})), \
                patch.object(pdf, '_extract_ocr', side_effect=ExtractionError('renderer failed')):
            with self.assertRaisesRegex(ExtractionError, 'renderer failed'):
                pdf._extract_bytes(b'scan')
        with patch.object(pdf, '_extract_text_pypdf', return_value=('readable text ' * 20, {})), \
                patch.object(pdf, '_extract_ocr', side_effect=AssertionError('unnecessary OCR')):
            self.assertIn('readable text', pdf._extract_bytes(b'text').text)

    def test_a_tall_image_is_read_in_bands_without_shrinking_or_repeating_lines(self):
        from types import SimpleNamespace
        from PIL import Image
        from imprint.extractors import ocr
        height = 6100
        # Each row's first pixel names the row, so the fake engine can tell
        # which band of the page it was handed.
        page = Image.new('RGB', (900, height), 'white')
        for y in range(height):
            page.putpixel((0, y), (y // 256, y % 256, 0))
        # Lines of 40 px every 70 px, plus a 190 px heading across the first seam.
        lines = [(y, y + 40, f'line {y}') for y in range(10, height - 40, 70)] + [(1700, 1890, 'heading')]
        calls = []

        def engine(band):
            top = band.getpixel((0, 0))[0] * 256 + band.getpixel((0, 0))[1]
            calls.append(band.size)
            seen = [(max(a, top) - top, min(b, top + band.size[1]) - top, text)
                    for a, b, text in lines if a < top + band.size[1] and b > top]
            # A line cut by the band's edge is still detected, as its visible part.
            return SimpleNamespace(boxes=[[(0, a), (9, a), (9, b), (0, b)] for a, b, _ in seen] or None,
                                   txts=tuple(text for *_, text in seen) or None)

        with patch.object(ocr, '_engine', return_value=engine):
            read = ocr.recognize(page).split('\n')
            self.assertEqual(sorted(read), sorted(text for *_, text in lines))
            self.assertTrue(all(h <= ocr.BAND_HEIGHT for _, h in calls) and len(calls) == 4)
            covered = ocr.bands(height)
            self.assertEqual((covered[0][2], covered[-1][3]), (0, height))
            self.assertTrue(all(a[3] == b[2] and a[1] - b[0] >= ocr.BAND_OVERLAP for a, b in zip(covered, covered[1:])))
            calls.clear()
            short = page.crop((0, 0, 900, 2000))
            self.assertEqual(ocr.recognize(short).split('\n'), [text for a, b, text in lines if a < 2000])
            self.assertEqual(calls, [(900, 2000)])

    def test_backend_and_availability_invalidate_old_empty_results(self):
        with patch.dict(os.environ, {'IMPRINT_INGEST_OCR': 'true', 'IMPRINT_INGEST_OCR_LANG': 'eng'}), \
                patch('imprint.extractors.ocr.available', return_value=True):
            profile = source_state.extraction_profile()
        self.assertEqual(profile['ocr_backend'], 'rapidocr-3.10.0-pdfium')
        self.assertTrue(profile['ocr_tools'])


if __name__ == '__main__':
    unittest.main()
