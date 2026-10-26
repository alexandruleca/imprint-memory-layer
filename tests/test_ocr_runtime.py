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

    def test_pdf_ocr_errors_propagate_and_text_layer_skips_ocr(self):
        with patch.object(pdf, '_ocr_enabled', return_value=True), \
                patch.object(pdf, '_extract_text_pypdf', return_value=('', {'page_count': 1})), \
                patch.object(pdf, '_extract_ocr', side_effect=ExtractionError('renderer failed')):
            with self.assertRaisesRegex(ExtractionError, 'renderer failed'):
                pdf._extract_bytes(b'scan')
        with patch.object(pdf, '_extract_text_pypdf', return_value=('readable text ' * 20, {})), \
                patch.object(pdf, '_extract_ocr', side_effect=AssertionError('unnecessary OCR')):
            self.assertIn('readable text', pdf._extract_bytes(b'text').text)

    def test_backend_and_availability_invalidate_old_empty_results(self):
        with patch.dict(os.environ, {'IMPRINT_INGEST_OCR': 'true', 'IMPRINT_INGEST_OCR_LANG': 'eng'}), \
                patch('imprint.extractors.ocr.available', return_value=True):
            profile = source_state.extraction_profile()
        self.assertEqual(profile['ocr_backend'], 'rapidocr-3.10.0-pdfium')
        self.assertTrue(profile['ocr_tools'])


if __name__ == '__main__':
    unittest.main()
